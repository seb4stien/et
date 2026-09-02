/* et GNOME Shell extension.
 *
 * Exposes a small D-Bus service, on GNOME Shell's own session-bus
 * connection (no separate bus name needed — the same pattern used by the
 * third-party "Window Calls" extension), for state that `et` can't
 * otherwise reach under Wayland. Today that's just the active workspace
 * index, standing in for what `wmctrl -d` gives on X11; more methods
 * (e.g. native time-tracking, replacing the third-party Tracker
 * extension) may be added here in later iterations.
 */

import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const DBUS_INTERFACE_XML = `
<node>
  <interface name="org.gnome.Shell.Extensions.Et">
    <method name="GetActiveWorkspaceIndex">
      <arg type="u" direction="out" name="index"/>
    </method>
  </interface>
</node>`;

const DBUS_OBJECT_PATH = '/org/gnome/Shell/Extensions/Et';

export default class EtExtension extends Extension {
    enable() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(DBUS_INTERFACE_XML, this);
        this._dbusImpl.export(Gio.DBus.session, DBUS_OBJECT_PATH);
    }

    disable() {
        this._dbusImpl?.unexport();
        this._dbusImpl = null;
    }

    GetActiveWorkspaceIndex() {
        return global.workspace_manager.get_active_workspace_index();
    }
}
