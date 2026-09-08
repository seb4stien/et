// SPDX-FileCopyrightText: 2026 Sébastien Georget
// SPDX-License-Identifier: GPL-3.0-or-later

/* Extension-owned persistent per-workspace counters.
 *
 * Each "prepared" workspace has an entry keyed by its GNOME workspace index
 * holding a generic display label, a reference estimate, and an elapsed
 * counter. Entries are persisted as a single JSON blob in the extension's
 * GSettings so they survive a GNOME Shell restart; the live "is this
 * counter currently running" state is deliberately *not* persisted; it is
 * always false right after (re)load; `WorkspaceStore` re-derives who should
 * be running from whatever workspace is active (see `syncRunning`).
 *
 * Timing uses `GLib.get_monotonic_time()` (microseconds since an
 * unspecified starting point, unaffected by wall-clock/timezone changes)
 * rather than wall-clock time, so counters can't skew from NTP adjustments,
 * DST, or the user changing the system clock.
 */

import GLib from 'gi://GLib';

const SETTINGS_KEY = 'workspaces';

/**
 * @typedef {object} WorkspaceEntry
 * @property {string} label
 * @property {number} estimateSeconds
 * @property {number} elapsedSeconds - Elapsed time accumulated *before* the
 *   current running span, if any (see `runningSinceUs`).
 * @property {number|null} runningSinceUs - Monotonic timestamp (µs) the
 *   counter started running, or `null` if it isn't currently running.
 */

export class WorkspaceValidationError extends Error {
    constructor(message) {
        super(message);
        this.name = 'org.gnome.Shell.Extensions.Et.Error.InvalidArgument';
    }
}

export class WorkspaceNotFoundError extends Error {
    constructor(message) {
        super(message);
        this.name = 'org.gnome.Shell.Extensions.Et.Error.NotFound';
    }
}

function requireWorkspaceIndex(index) {
    const numericIndex = Number(index);
    if (!Number.isInteger(numericIndex) || numericIndex < 0)
        throw new WorkspaceValidationError(`invalid workspace index: ${index}`);
    return numericIndex;
}

function requireEstimateSeconds(estimateSeconds) {
    const numericEstimate = Number(estimateSeconds);
    if (!Number.isFinite(numericEstimate) || numericEstimate < 0)
        throw new WorkspaceValidationError(`invalid estimate seconds: ${estimateSeconds}`);
    return numericEstimate;
}

function requireLabel(label) {
    if (typeof label !== 'string')
        throw new WorkspaceValidationError('label must be a string');
    return label;
}

/** Manages the persisted per-workspace counter store for one GSettings object. */
export class WorkspaceStore {
    constructor(settings, monotonicTime = GLib.get_monotonic_time) {
        this._settings = settings;
        this._monotonicTime = monotonicTime;
        /** @type {Map<number, WorkspaceEntry>} */
        this._entries = this._load();
    }

    _load() {
        const entries = new Map();
        let raw;
        try {
            raw = JSON.parse(this._settings.get_string(SETTINGS_KEY) || '{}');
        } catch {
            raw = {};
        }
        if (raw && typeof raw === 'object') {
            for (const [key, value] of Object.entries(raw)) {
                const index = Number(key);
                if (!Number.isInteger(index) || index < 0)
                    continue;
                entries.set(index, {
                    label: typeof value?.label === 'string' ? value.label : '',
                    estimateSeconds: Number.isFinite(value?.estimateSeconds)
                        ? Math.max(0, value.estimateSeconds) : 0,
                    elapsedSeconds: Number.isFinite(value?.elapsedSeconds)
                        ? Math.max(0, value.elapsedSeconds) : 0,
                    runningSinceUs: null,
                });
            }
        }
        return entries;
    }

    /** Persists the current entries (elapsed seconds only; never the running state). */
    save() {
        const serializable = {};
        for (const [index, entry] of this._entries) {
            serializable[index] = {
                label: entry.label,
                estimateSeconds: entry.estimateSeconds,
                elapsedSeconds: entry.elapsedSeconds,
            };
        }
        this._settings.set_string(SETTINGS_KEY, JSON.stringify(serializable));
    }

    has(index) {
        return this._entries.has(requireWorkspaceIndex(index));
    }

    /** Returns the entry for `index`, or `undefined` if it isn't prepared. */
    get(index) {
        return this._entries.get(requireWorkspaceIndex(index));
    }

    /** Returns all prepared indices. */
    indices() {
        return [...this._entries.keys()];
    }

    /**
     * (Re)creates the counter for `index` at zero with the given
     * label/estimate, whether or not one already existed. A workspace slot
     * that's being reused for a new task must never inherit the previous
     * task's accumulated elapsed time, so this always zeroes it rather than
     * only doing so for brand-new slots; use `setMetadata` instead to
     * update label/estimate without touching an already-running counter.
     * Returns the entry.
     */
    prepare(index, label, estimateSeconds) {
        const numericIndex = requireWorkspaceIndex(index);
        const safeLabel = requireLabel(label);
        const safeEstimate = requireEstimateSeconds(estimateSeconds);

        const entry = {label: safeLabel, estimateSeconds: safeEstimate, elapsedSeconds: 0, runningSinceUs: null};
        this._entries.set(numericIndex, entry);
        return entry;
    }

    /** Updates label/estimate for an already-prepared workspace. */
    setMetadata(index, label, estimateSeconds) {
        const entry = this._requireEntry(index);
        entry.label = requireLabel(label);
        entry.estimateSeconds = requireEstimateSeconds(estimateSeconds);
        return entry;
    }

    /**
     * Resets a prepared workspace's elapsed time and running state to zero
     * (stopped). Callers that want the counter to keep running afterward
     * (e.g. because its workspace is still the active, unlocked one) should
     * re-derive that separately (see `EtExtension._syncRunningState`)
     * rather than relying on this to preserve it.
     */
    resetElapsed(index) {
        const entry = this._requireEntry(index);
        entry.elapsedSeconds = 0;
        entry.runningSinceUs = null;
        return entry;
    }

    /**
     * Removes a workspace's counter entirely. Unlike the other mutators,
     * this never range-checks or errors on an unknown index: it exists
     * precisely to clean up stale/out-of-range entries (e.g. after GNOME's
     * workspace count shrinks), so removing an already-absent entry is a
     * harmless no-op. Returns whether an entry was actually removed.
     */
    remove(index) {
        return this._entries.delete(requireWorkspaceIndex(index));
    }

    /**
     * Atomically moves counters between workspace indices, e.g. when
     * workspaces are reordered/shifted. `moves` is a list of `{from, to}`
     * pairs, applied as a single simultaneous permutation: a `from` index
     * with no prepared entry clears whatever previously sat at its `to`
     * index (shift semantics), matching a slot becoming genuinely empty.
     */
    remap(moves) {
        const seenFrom = new Set();
        const seenTo = new Set();
        const normalizedMoves = [];
        for (const move of moves) {
            const from = requireWorkspaceIndex(move[0]);
            const to = requireWorkspaceIndex(move[1]);
            if (from === to)
                continue;
            if (seenFrom.has(from))
                throw new WorkspaceValidationError(`duplicate source index in RemapWorkspaces: ${from}`);
            if (seenTo.has(to))
                throw new WorkspaceValidationError(`duplicate destination index in RemapWorkspaces: ${to}`);
            seenFrom.add(from);
            seenTo.add(to);
            normalizedMoves.push({from, to});
        }

        const previousEntries = this._entries;
        const nextEntries = new Map();
        for (const [index, entry] of previousEntries) {
            if (!seenFrom.has(index))
                nextEntries.set(index, entry);
        }
        for (const {from, to} of normalizedMoves) {
            const entry = previousEntries.get(from);
            if (entry !== undefined)
                nextEntries.set(to, entry);
            else
                nextEntries.delete(to);
        }
        this._entries = nextEntries;
    }

    /** Returns `elapsedSeconds` including any currently-running span. */
    liveElapsedSeconds(index) {
        const entry = this._requireEntry(index);
        return this._liveElapsedSeconds(entry);
    }

    _liveElapsedSeconds(entry) {
        if (entry.runningSinceUs === null)
            return entry.elapsedSeconds;
        const elapsedUs = this._monotonicTime() - entry.runningSinceUs;
        return entry.elapsedSeconds + Math.max(0, elapsedUs) / 1e6;
    }

    isRunning(index) {
        return this._requireEntry(index).runningSinceUs !== null;
    }

    /** Starts the counter for `index`, if it isn't already running. */
    start(index) {
        const entry = this._requireEntry(index);
        if (entry.runningSinceUs === null)
            entry.runningSinceUs = this._monotonicTime();
    }

    /** Stops the counter for `index`, folding the running span into `elapsedSeconds`. */
    stop(index) {
        const entry = this._requireEntry(index);
        if (entry.runningSinceUs !== null) {
            entry.elapsedSeconds = this._liveElapsedSeconds(entry);
            entry.runningSinceUs = null;
        }
    }

    /** Stops every running counter (e.g. before disable() tears everything down). */
    stopAll() {
        for (const index of this._entries.keys())
            this.stop(index);
    }

    /**
     * Folds any running span into `elapsedSeconds` and restarts the running
     * span from now, without changing whether the counter is running. Used
     * for periodic checkpoints so a crash/restart loses only a bounded
     * window of time instead of the whole running span.
     */
    checkpoint() {
        for (const entry of this._entries.values()) {
            if (entry.runningSinceUs !== null) {
                entry.elapsedSeconds = this._liveElapsedSeconds(entry);
                entry.runningSinceUs = this._monotonicTime();
            }
        }
    }

    _requireEntry(index) {
        const numericIndex = requireWorkspaceIndex(index);
        const entry = this._entries.get(numericIndex);
        if (!entry)
            throw new WorkspaceNotFoundError(`workspace ${numericIndex} has no prepared counter`);
        return entry;
    }
}
