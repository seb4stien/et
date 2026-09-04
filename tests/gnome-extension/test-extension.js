// SPDX-FileCopyrightText: 2026 Sébastien Georget
// SPDX-License-Identifier: GPL-3.0-or-later

import {buildDisplayText, formatDuration} from '../../gnome-extension/et@seb4stien.github.com/lib/display.js';
import {
    WorkspaceNotFoundError,
    WorkspaceStore,
    WorkspaceValidationError,
} from '../../gnome-extension/et@seb4stien.github.com/lib/workspaceStore.js';

class FakeSettings {
    constructor(workspaces = '{}', booleans = {}) {
        this._workspaces = workspaces;
        this._booleans = booleans;
    }

    get_string(key) {
        if (key !== 'workspaces')
            throw new Error(`unexpected string setting: ${key}`);
        return this._workspaces;
    }

    set_string(key, value) {
        if (key !== 'workspaces')
            throw new Error(`unexpected string setting: ${key}`);
        this._workspaces = value;
    }

    get_boolean(key) {
        return this._booleans[key] ?? false;
    }
}

function fail(message) {
    throw new Error(message);
}

function assertEqual(actual, expected, message = '') {
    if (actual !== expected)
        fail(`${message} expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

function assertDeepEqual(actual, expected, message = '') {
    assertEqual(JSON.stringify(actual), JSON.stringify(expected), message);
}

function assertThrows(callback, ErrorType, messagePart) {
    try {
        callback();
    } catch (error) {
        if (!(error instanceof ErrorType))
            fail(`expected ${ErrorType.name}, got ${error.constructor.name}: ${error.message}`);
        if (messagePart && !error.message.includes(messagePart))
            fail(`expected error containing ${JSON.stringify(messagePart)}, got ${error.message}`);
        return;
    }
    fail(`expected ${ErrorType.name} to be thrown`);
}

const tests = [];

function test(name, callback) {
    tests.push([name, callback]);
}

test('formatDuration formats minutes, hours, and negative input', () => {
    assertEqual(formatDuration(0), '0m');
    assertEqual(formatDuration(3599), '59m');
    assertEqual(formatDuration(3600), '1h 00m');
    assertEqual(formatDuration(7265), '2h 01m');
    assertEqual(formatDuration(-20), '0m');
});

test('buildDisplayText respects every display toggle', () => {
    const entry = {label: 'Task', estimateSeconds: 7200};
    assertEqual(buildDisplayText(null, 60, new FakeSettings()), '');
    assertEqual(buildDisplayText(entry, 60, new FakeSettings()), '');
    assertEqual(buildDisplayText(entry, 60, new FakeSettings('{}', {
        'show-label': true,
        'show-estimate': true,
        'show-counter': true,
    })), 'Task · 2h 00m · 1m');
    assertEqual(buildDisplayText(
        {label: '', estimateSeconds: 0},
        60,
        new FakeSettings('{}', {
            'show-label': true,
            'show-estimate': true,
            'show-counter': true,
        }),
    ), '1m');
});

test('WorkspaceStore sanitizes persisted state', () => {
    const settings = new FakeSettings(JSON.stringify({
        0: {label: 'Task', estimateSeconds: 20, elapsedSeconds: 10},
        1: {label: 3, estimateSeconds: -5, elapsedSeconds: -2},
        invalid: {label: 'ignored'},
        '-1': {label: 'ignored'},
    }));
    const store = new WorkspaceStore(settings);
    assertDeepEqual(store.indices(), [0, 1]);
    assertDeepEqual(store.get(0), {
        label: 'Task',
        estimateSeconds: 20,
        elapsedSeconds: 10,
        runningSinceUs: null,
    });
    assertDeepEqual(store.get(1), {
        label: '',
        estimateSeconds: 0,
        elapsedSeconds: 0,
        runningSinceUs: null,
    });
});

test('WorkspaceStore tolerates corrupt persisted JSON', () => {
    const store = new WorkspaceStore(new FakeSettings('{broken'));
    assertDeepEqual(store.indices(), []);
});

test('WorkspaceStore mutators validate and persist entries', () => {
    const settings = new FakeSettings();
    const store = new WorkspaceStore(settings);
    store.prepare(1, 'Task', 90);
    store.setMetadata(1, 'Renamed', 120);
    store.save();
    assertDeepEqual(JSON.parse(settings._workspaces), {
        1: {label: 'Renamed', estimateSeconds: 120, elapsedSeconds: 0},
    });
    store.resetElapsed(1);
    assertEqual(store.remove(1), true);
    assertEqual(store.remove(1), false);
    assertThrows(() => store.prepare(-1, 'Task', 0), WorkspaceValidationError, 'index');
    assertThrows(() => store.prepare(0, 3, 0), WorkspaceValidationError, 'label');
    assertThrows(() => store.prepare(0, 'Task', -1), WorkspaceValidationError, 'estimate');
    assertThrows(() => store.get(0.5), WorkspaceValidationError, 'index');
    assertThrows(() => store.liveElapsedSeconds(0), WorkspaceNotFoundError, 'no prepared');
});

test('WorkspaceStore start, stop, checkpoint, and reload use monotonic time', () => {
    let nowUs = 1_000_000;
    const settings = new FakeSettings();
    const store = new WorkspaceStore(settings, () => nowUs);
    store.prepare(0, 'Task', 0);
    store.start(0);
    assertEqual(store.isRunning(0), true);
    nowUs = 3_500_000;
    assertEqual(store.liveElapsedSeconds(0), 2.5);
    store.checkpoint();
    store.save();
    nowUs = 5_000_000;
    assertEqual(store.liveElapsedSeconds(0), 4);
    store.stop(0);
    assertEqual(store.liveElapsedSeconds(0), 4);

    const reloaded = new WorkspaceStore(settings, () => nowUs);
    assertEqual(reloaded.liveElapsedSeconds(0), 2.5);
    assertEqual(reloaded.isRunning(0), false);
});

test('WorkspaceStore ignores negative monotonic deltas', () => {
    let nowUs = 5_000_000;
    const store = new WorkspaceStore(new FakeSettings(), () => nowUs);
    store.prepare(0, 'Task', 0);
    store.start(0);
    nowUs = 4_000_000;
    assertEqual(store.liveElapsedSeconds(0), 0);
});

test('WorkspaceStore remaps entries simultaneously', () => {
    const store = new WorkspaceStore(new FakeSettings());
    store.prepare(0, 'Zero', 0);
    store.prepare(1, 'One', 0);
    store.prepare(3, 'Three', 0);
    store.remap([[0, 1], [1, 2], [2, 3]]);
    assertEqual(store.has(0), false);
    assertEqual(store.get(1).label, 'Zero');
    assertEqual(store.get(2).label, 'One');
    assertEqual(store.has(3), false);
    assertThrows(
        () => store.remap([[0, 1], [0, 2]]),
        WorkspaceValidationError,
        'duplicate source',
    );
    assertThrows(
        () => store.remap([[0, 2], [1, 2]]),
        WorkspaceValidationError,
        'duplicate destination',
    );
});

let failures = 0;
for (const [name, callback] of tests) {
    try {
        callback();
        print(`PASS: ${name}`);
    } catch (error) {
        failures++;
        printerr(`FAIL: ${name}\n${error.stack ?? error}`);
    }
}

if (failures > 0)
    throw new Error(`${failures} GJS test(s) failed`);

print(`All ${tests.length} GJS tests passed.`);
