// SPDX-FileCopyrightText: 2026 Sébastien Georget
// SPDX-License-Identifier: GPL-3.0-or-later

/* Augments GNOME's native workspace-switcher popup (the small row of dots
 * shown briefly on a workspace-switch keybinding) with a text label, without
 * replacing or forking it.
 *
 * The popup's internal shape differs between GNOME 46 and GNOME 50:
 *  - GNOME 46: a single `WorkspaceSwitcherPopup` actor owns one `_list`
 *    (an `St.BoxLayout`) holding one indicator per workspace.
 *  - GNOME 50: the outer `WorkspaceSwitcherPopup` is itself a container of
 *    one `MonitorWorkspaceSwitcherPopup` child per targeted monitor, each
 *    with its own `_list`.
 * `getIndicatorLists()` below is the compatibility adapter that normalizes
 * both shapes to "a list of `_list`s to add our label to". It uses feature
 * detection (does the popup have its own `_list`, or is it iterable?)
 * rather than branching on the Shell's version number, so it keeps working
 * across point releases that don't change this shape.
 */

import Clutter from 'gi://Clutter';
import St from 'gi://St';

import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as WorkspaceSwitcherPopup from 'resource:///org/gnome/shell/ui/workspaceSwitcherPopup.js';

const LABEL_STYLE_CLASS = 'et-workspace-switcher-label';

function getIndicatorLists(popup) {
    if (popup._list)
        return [popup._list];
    if (typeof popup[Symbol.iterator] === 'function')
        return [...popup].map(monitorPopup => monitorPopup._list).filter(Boolean);
    return [];
}

/**
 * Adds a text label (from `getLabelText(activeWorkspaceIndex)`, called each
 * time the popup is shown) alongside the native workspace-switcher popup's
 * indicator dots. Uses `InjectionManager` (from the extension base module)
 * to wrap `WorkspaceSwitcherPopup.prototype.display` so the original method
 * and its behaviour are fully preserved and can be cleanly restored.
 */
export class WorkspaceSwitcherLabeler {
    constructor(getLabelText) {
        this._getLabelText = getLabelText;
        this._injectionManager = null;
        this._labels = new Set();
    }

    enable() {
        this._injectionManager = new InjectionManager();
        const labeler = this;
        this._injectionManager.overrideMethod(
            WorkspaceSwitcherPopup.WorkspaceSwitcherPopup.prototype, 'display',
            originalDisplay => function (activeWorkspaceIndex) {
                originalDisplay.call(this, activeWorkspaceIndex);
                labeler._onDisplay(this, activeWorkspaceIndex);
            });
    }

    disable() {
        this._injectionManager?.clear();
        this._injectionManager = null;
        for (const label of this._labels)
            label.destroy();
        this._labels.clear();
    }

    _onDisplay(popup, activeWorkspaceIndex) {
        let text;
        try {
            text = this._getLabelText(activeWorkspaceIndex);
        } catch (error) {
            console.warn(`et: failed to compute workspace switcher label: ${error}`);
            return;
        }

        try {
            for (const list of getIndicatorLists(popup)) {
                const previous = list.get_children().find(
                    child => child.has_style_class_name(LABEL_STYLE_CLASS));
                previous?.destroy();

                if (!text)
                    continue;

                const label = new St.Label({
                    style_class: LABEL_STYLE_CLASS,
                    text,
                    y_align: Clutter.ActorAlign.CENTER,
                });
                label.connect('destroy', () => this._labels.delete(label));
                this._labels.add(label);
                list.add_child(label);
            }
        } catch (error) {
            // `_list` is a private implementation detail of GNOME Shell's own
            // popup; fail soft rather than breaking that popup if a future
            // point release changes its shape in a way our adapter above
            // doesn't already handle.
            console.warn(`et: failed to update workspace switcher label: ${error}`);
        }
    }
}
