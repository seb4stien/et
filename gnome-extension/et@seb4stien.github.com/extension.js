// SPDX-FileCopyrightText: 2026 Sébastien Georget
// SPDX-License-Identifier: GPL-3.0-or-later

/* et GNOME Shell extension.
 *
 * Exposes a small D-Bus service, on GNOME Shell's own session-bus
 * connection (no separate bus name needed — the same pattern used by the
 * third-party "Window Calls" extension), for state that `et` can't
 * otherwise reach under Wayland: the active workspace index, standing in
 * for what `wmctrl -d` gives on X11.
 *
 * It also owns persistent per-workspace counters, replacing the
 * third-party Tracker extension: any workspace `et` "prepares" gets a
 * generic label, a reference estimate, and an elapsed-time counter that
 * runs automatically while that workspace is active, pausing only while
 * the session is locked (ordinary keyboard/mouse inactivity still counts).
 * The counter, label, and estimate can be shown in the top panel and/or
 * GNOME's workspace-switcher popup, independently configurable via
 * GSettings/`prefs.js`.
 */

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

import {buildDisplayText} from './lib/display.js';
import {WorkspaceStore, WorkspaceNotFoundError, WorkspaceValidationError} from './lib/workspaceStore.js';
import {WorkspaceSwitcherLabeler} from './lib/workspaceSwitcherCompat.js';

const DBUS_INTERFACE_XML = `
<node>
  <interface name="org.gnome.Shell.Extensions.Et">
    <method name="GetActiveWorkspaceIndex">
      <arg type="u" direction="out" name="index"/>
    </method>
    <method name="PrepareWorkspace">
      <arg type="u" direction="in" name="index"/>
      <arg type="s" direction="in" name="label"/>
      <arg type="t" direction="in" name="estimateSeconds"/>
    </method>
    <method name="GetWorkspaceCounter">
      <arg type="u" direction="in" name="index"/>
      <arg type="t" direction="out" name="elapsedSeconds"/>
      <arg type="b" direction="out" name="running"/>
    </method>
    <method name="GetWorkspaceMetadata">
      <arg type="u" direction="in" name="index"/>
      <arg type="s" direction="out" name="label"/>
      <arg type="t" direction="out" name="estimateSeconds"/>
    </method>
    <method name="ResetWorkspaceCounter">
      <arg type="u" direction="in" name="index"/>
    </method>
    <method name="RemoveWorkspace">
      <arg type="u" direction="in" name="index"/>
    </method>
    <method name="RemapWorkspaces">
      <arg type="a(uu)" direction="in" name="moves"/>
    </method>
    <method name="SetWorkspaceMetadata">
      <arg type="u" direction="in" name="index"/>
      <arg type="s" direction="in" name="label"/>
      <arg type="t" direction="in" name="estimateSeconds"/>
    </method>
  </interface>
</node>`;

const DBUS_OBJECT_PATH = '/org/gnome/Shell/Extensions/Et';
const PANEL_ROLE = 'et@seb4stien.github.com';
const PANEL_UPDATE_INTERVAL_SECONDS = 1;
const CHECKPOINT_INTERVAL_SECONDS = 30;
const DISPLAY_SETTINGS_KEYS = ['show-label', 'show-estimate', 'show-counter', 'show-panel'];

export default class EtExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._store = new WorkspaceStore(this._settings);

        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(DBUS_INTERFACE_XML, this);
        this._dbusImpl.export(Gio.DBus.session, DBUS_OBJECT_PATH);

        this._indicator = new PanelMenu.Button(0.5, 'et Workspace Timer', true);
        this._panelBox = new St.BoxLayout({
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelIcon = new St.Icon({
            style_class: 'et-panel-icon',
            icon_name: 'preferences-system-time-symbolic',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelLabel = new St.Label({
            style_class: 'et-panel-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelBox.add_child(this._panelIcon);
        this._panelBox.add_child(this._panelLabel);
        this._indicator.add_child(this._panelBox);
        Main.panel.addToStatusArea(PANEL_ROLE, this._indicator);

        this._switcherLabeler = new WorkspaceSwitcherLabeler(
            activeIndex => this._getSwitcherText(activeIndex));
        this._switcherLabeler.enable();

        this._workspaceManagerSignalId = global.workspace_manager.connect(
            'active-workspace-changed', () => this._syncRunningState());
        this._screenShieldSignalId = Main.screenShield?.connect(
            'locked-changed', () => this._syncRunningState()) ?? null;
        this._settingsSignalId = this._settings.connect('changed', (settings, key) => {
            if (DISPLAY_SETTINGS_KEYS.includes(key))
                this._updatePanel();
        });

        this._panelUpdateSourceId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, PANEL_UPDATE_INTERVAL_SECONDS, () => {
                this._updatePanel();
                return GLib.SOURCE_CONTINUE;
            });
        this._checkpointSourceId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, CHECKPOINT_INTERVAL_SECONDS, () => {
                this._store.checkpoint();
                this._store.save();
                return GLib.SOURCE_CONTINUE;
            });

        this._syncRunningState();
    }

    disable() {
        if (this._panelUpdateSourceId) {
            GLib.source_remove(this._panelUpdateSourceId);
            this._panelUpdateSourceId = null;
        }
        if (this._checkpointSourceId) {
            GLib.source_remove(this._checkpointSourceId);
            this._checkpointSourceId = null;
        }
        if (this._workspaceManagerSignalId) {
            global.workspace_manager.disconnect(this._workspaceManagerSignalId);
            this._workspaceManagerSignalId = null;
        }
        if (this._screenShieldSignalId) {
            Main.screenShield?.disconnect(this._screenShieldSignalId);
            this._screenShieldSignalId = null;
        }
        if (this._settingsSignalId) {
            this._settings.disconnect(this._settingsSignalId);
            this._settingsSignalId = null;
        }

        this._switcherLabeler?.disable();
        this._switcherLabeler = null;

        this._indicator?.destroy();
        this._indicator = null;
        this._panelBox = null;
        this._panelIcon = null;
        this._panelLabel = null;

        if (this._store) {
            this._store.stopAll();
            this._store.save();
        }
        this._store = null;

        this._dbusImpl?.unexport();
        this._dbusImpl = null;

        this._settings = null;
    }

    // --- D-Bus methods (org.gnome.Shell.Extensions.Et) -------------------

    GetActiveWorkspaceIndex() {
        return global.workspace_manager.get_active_workspace_index();
    }

    PrepareWorkspace(index, label, estimateSeconds) {
        this._store.prepare(this._requireExistingWorkspaceIndex(index), label, estimateSeconds);
        this._store.save();
        this._syncRunningState();
    }

    GetWorkspaceCounter(index) {
        const numericIndex = Number(index);
        const elapsedSeconds = this._store.liveElapsedSeconds(numericIndex);
        const running = this._store.isRunning(numericIndex);
        return [BigInt(Math.floor(elapsedSeconds)), running];
    }

    GetWorkspaceMetadata(index) {
        const numericIndex = Number(index);
        if (!this._store.has(numericIndex))
            throw new WorkspaceNotFoundError(`workspace ${numericIndex} has no prepared counter`);
        const entry = this._store.get(numericIndex);
        return [entry.label, BigInt(Math.floor(entry.estimateSeconds))];
    }

    ResetWorkspaceCounter(index) {
        this._store.resetElapsed(Number(index));
        this._store.save();
        this._syncRunningState();
    }

    RemoveWorkspace(index) {
        const numericIndex = Number(index);
        if (this._store.has(numericIndex) && this._store.isRunning(numericIndex))
            this._store.stop(numericIndex);
        this._store.remove(numericIndex);
        this._store.save();
        this._syncRunningState();
    }

    RemapWorkspaces(moves) {
        this._store.remap(moves);
        this._store.save();
        this._syncRunningState();
    }

    SetWorkspaceMetadata(index, label, estimateSeconds) {
        this._store.setMetadata(this._requireExistingWorkspaceIndex(index), label, estimateSeconds);
        this._store.save();
        this._updatePanel();
    }

    // --- Internal helpers --------------------------------------------------

    _requireExistingWorkspaceIndex(index) {
        const numericIndex = Number(index);
        const workspaceCount = global.workspace_manager.n_workspaces;
        if (!Number.isInteger(numericIndex) || numericIndex < 0 || numericIndex >= workspaceCount)
            throw new WorkspaceValidationError(`workspace index out of range: ${index}`);
        return numericIndex;
    }

    /** Starts/stops counters so only the active, unlocked workspace's counter runs. */
    _syncRunningState() {
        if (!this._store)
            return;

        const activeIndex = global.workspace_manager.get_active_workspace_index();
        const locked = Main.screenShield?.locked ?? false;
        let changed = false;
        for (const index of this._store.indices()) {
            const shouldRun = !locked && index === activeIndex;
            const isRunning = this._store.isRunning(index);
            if (shouldRun && !isRunning) {
                this._store.start(index);
                changed = true;
            } else if (!shouldRun && isRunning) {
                this._store.stop(index);
                changed = true;
            }
        }
        if (changed)
            this._store.save();
        this._updatePanel();
    }

    _updatePanel() {
        if (!this._indicator || !this._store)
            return;

        const activeIndex = global.workspace_manager.get_active_workspace_index();
        const entry = this._store.has(activeIndex) ? this._store.get(activeIndex) : null;
        const elapsedSeconds = entry ? this._store.liveElapsedSeconds(activeIndex) : 0;
        const text = buildDisplayText(entry, elapsedSeconds, this._settings);

        this._panelLabel.text = text;
        this._panelLabel.visible = text.length > 0;
        // The icon stays visible whenever the panel display is enabled, even
        // with no prepared workspace, as a persistent "extension is active"
        // indicator; only the label (which needs a prepared workspace to say
        // anything useful) collapses away.
        this._indicator.visible = this._settings.get_boolean('show-panel');
    }

    _getSwitcherText(activeIndex) {
        if (!this._settings.get_boolean('show-workspace-switcher') || !this._store)
            return '';
        if (!this._store.has(activeIndex))
            return '';

        const entry = this._store.get(activeIndex);
        const elapsedSeconds = this._store.liveElapsedSeconds(activeIndex);
        return buildDisplayText(entry, elapsedSeconds, this._settings);
    }
}
