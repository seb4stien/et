// SPDX-FileCopyrightText: 2026 Sébastien Georget
// SPDX-License-Identifier: GPL-3.0-or-later

/* Preferences window for the et GNOME Shell extension.
 *
 * Runs out-of-process (via `gnome-extensions prefs`), so it uses the
 * `resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js` helper
 * module rather than the main-process `.../shell/extensions/extension.js`
 * one `extension.js` uses.
 */

import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences, gettext as _} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const EtPrefsPage = GObject.registerClass(
class EtPrefsPage extends Adw.PreferencesPage {
    _init(settings) {
        super._init({
            title: _('et Workspace Timer'),
            icon_name: 'preferences-system-time-symbolic',
        });

        const contentGroup = new Adw.PreferencesGroup({
            title: _('Displayed information'),
            description: _(
                'The workspace label and reference estimate are set by the ' +
                'et CLI when it prepares a workspace; the counter tracks ' +
                'elapsed time automatically.'),
        });
        this.add(contentGroup);
        contentGroup.add(this._switchRow(settings, 'show-label', _('Show workspace label')));
        contentGroup.add(this._switchRow(settings, 'show-estimate', _('Show reference estimate')));
        contentGroup.add(this._switchRow(settings, 'show-counter', _('Show elapsed counter')));

        const locationGroup = new Adw.PreferencesGroup({
            title: _('Where to show it'),
        });
        this.add(locationGroup);
        locationGroup.add(this._switchRow(settings, 'show-panel', _('Top panel')));
        locationGroup.add(this._switchRow(settings, 'show-workspace-switcher', _('Workspace switcher')));
    }

    _switchRow(settings, key, title) {
        const toggle = new Gtk.Switch({valign: Gtk.Align.CENTER});
        settings.bind(key, toggle, 'active', Gio.SettingsBindFlags.DEFAULT);

        const row = new Adw.ActionRow({title, activatable_widget: toggle});
        row.add_suffix(toggle);
        return row;
    }
});

export default class EtPrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.add(new EtPrefsPage(this.getSettings()));
    }
}
