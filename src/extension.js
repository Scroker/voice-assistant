/*
 * Voice Assistant GNOME Extension
 * Copyright (C) 2026 Giorgio Dramis
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';

// Funzione per l'installazione dei servizi systemd e dbus (stile GSConnect)
function setupDaemonServices(extensionDir) {
    let startScript = extensionDir.get_child('daemon').get_child('start.sh').get_path();
    let encoder = new TextEncoder();
    let decoder = new TextDecoder();
    let servicesDir = extensionDir.get_child('services');

    // 1. Install Systemd Service
    let systemdDir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_config_dir(), 'systemd', 'user']));
    if (!systemdDir.query_exists(null)) {
        systemdDir.make_directory_with_parents(null);
    }
    
    let systemdContent = `[Unit]\nDescription=Local Voice Assistant Daemon\nAfter=graphical-session.target\n\n[Service]\nType=dbus\nBusName=org.local.VoiceAssistant\nExecStart=${startScript}\nRestart=on-failure\n`;
    try {
        let bytes = Gio.resources_lookup_data('/org/gnome/shell/extensions/voice-assistant/services/voice-assistant.service.in', Gio.ResourceLookupFlags.NONE);
        systemdContent = decoder.decode(bytes.get_data()).replace(/@startScript@/g, startScript);
    } catch (e) {
        try {
            let systemdTpl = servicesDir.get_child('voice-assistant.service.in');
            if (systemdTpl.query_exists(null)) {
                let [, bytes] = systemdTpl.load_contents(null);
                systemdContent = decoder.decode(bytes).replace(/@startScript@/g, startScript);
            }
        } catch (err) { }
    }

    let systemdService = systemdDir.get_child('voice-assistant.service');
    systemdService.replace_contents(encoder.encode(systemdContent), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);

    // 2. Install DBus Service
    let dbusDir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_data_dir(), 'dbus-1', 'services']));
    if (!dbusDir.query_exists(null)) {
        dbusDir.make_directory_with_parents(null);
    }
    
    let dbusContent = `[D-BUS Service]\nName=org.local.VoiceAssistant\nExec=${startScript}\nSystemdService=voice-assistant.service\n`;
    try {
        let bytes = Gio.resources_lookup_data('/org/gnome/shell/extensions/voice-assistant/services/org.local.VoiceAssistant.service.in', Gio.ResourceLookupFlags.NONE);
        dbusContent = decoder.decode(bytes.get_data()).replace(/@startScript@/g, startScript);
    } catch (e) {
        try {
            let dbusTpl = servicesDir.get_child('org.local.VoiceAssistant.service.in');
            if (dbusTpl.query_exists(null)) {
                let [, bytes] = dbusTpl.load_contents(null);
                dbusContent = decoder.decode(bytes).replace(/@startScript@/g, startScript);
            }
        } catch (err) { }
    }

    let dbusService = dbusDir.get_child('org.local.VoiceAssistant.service');
    dbusService.replace_contents(encoder.encode(dbusContent), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);

    // 3. Reload systemd and start service
    try {
        let reloadProc = new Gio.Subprocess({
            argv: ['systemctl', '--user', 'daemon-reload'],
            flags: Gio.SubprocessFlags.NONE
        });
        reloadProc.init(null);
        reloadProc.wait_check_async(null, (proc, res) => {
            try {
                proc.wait_check_finish(res);
                let startProc = new Gio.Subprocess({
                    argv: ['systemctl', '--user', 'enable', '--now', 'voice-assistant.service'],
                    flags: Gio.SubprocessFlags.NONE
                });
                startProc.init(null);
            } catch (e) {
                console.error(`[VoiceAssistant] systemctl start failed: ${e.message}`);
            }
        });
    } catch(e) {
        console.error(`[VoiceAssistant] systemctl reload failed: ${e.message}`);
    }
}

function stopDaemonService() {
    try {
        let stopProc = new Gio.Subprocess({
            argv: ['systemctl', '--user', 'stop', 'voice-assistant.service'],
            flags: Gio.SubprocessFlags.NONE
        });
        stopProc.init(null);
    } catch(e) {
        console.error(`[VoiceAssistant] Failed to stop systemd service: ${e.message}`);
    }
}

// Definizione dell'interfaccia D-Bus (Caricata da GResource o fallback)
let VoiceAssistantIface;
try {
    let bytes = Gio.resources_lookup_data('/org/gnome/shell/extensions/voice-assistant/dbus/org.local.VoiceAssistant.xml', Gio.ResourceLookupFlags.NONE);
    VoiceAssistantIface = new TextDecoder().decode(bytes.get_data());
} catch (e) {
    VoiceAssistantIface = `
<node>
  <interface name="org.local.VoiceAssistant">
    <method name="ToggleListening">
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="GetAvailableModels">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetDownloadingModels">
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="DownloadModel">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <method name="CancelDownload">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <signal name="StateChanged">
      <arg type="s" name="new_state"/>
    </signal>
    <signal name="DownloadProgress">
      <arg type="s" name="provider"/>
      <arg type="s" name="model"/>
      <arg type="i" name="percent"/>
    </signal>
  </interface>
</node>`;
}

const VoiceAssistantProxy = Gio.DBusProxy.makeProxyWrapper(VoiceAssistantIface);

// Classe del pulsante nella Top Bar
const AssistantIndicator = GObject.registerClass(
    class AssistantIndicator extends PanelMenu.Button {
        _init(extension) {
            super._init(0.5, 'Voice Assistant Trigger');
            this._extension = extension;

            // Creazione icona grafica
            this._customGIcon = Gio.icon_new_for_string('resource:///org/gnome/shell/extensions/voice-assistant/icons/vocal-assistant-symbolic.svg');
            this._icon = new St.Icon({
                gicon: this._customGIcon,
                style_class: 'system-status-icon voice-assistant-indicator',
            });
            this.add_child(this._icon);

            this._settings = this._extension.getSettings('org.gnome.shell.extensions.voice-assistant');
            
            // Imposta lo stato visivo iniziale leggendo da GSettings
            let isEnabled = this._settings.get_boolean('enabled');
            this._updateUiState(isEnabled ? 'idle' : 'disabled');

            this._dbusProxy = null;
            this._signalId = null;

            // Costruzione del menu a tendina
            this._toggleItem = new PopupMenu.PopupMenuItem(_('Attiva / Disattiva'));
            this._toggleItem.connect('activate', () => {
                this._toggleRecording();
            });
            this.menu.addMenuItem(this._toggleItem);

            // Voce separata per il progresso di download (nascosta di default)
            this._downloadItem = new PopupMenu.PopupMenuItem(_(''));
            this._downloadItem.setSensitive(false);
            this._downloadItem.visible = false;
            this.menu.addMenuItem(this._downloadItem);

            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

            this._settingsItem = new PopupMenu.PopupMenuItem(_('Impostazioni'));
            this._settingsItem.connect('activate', () => {
                this._extension.openPreferences();
            });
            this.menu.addMenuItem(this._settingsItem);

            this._connectToDaemon();
        }

        _connectToDaemon() {
            this._watchId = Gio.bus_watch_name(
                Gio.BusType.SESSION,
                'org.local.VoiceAssistant',
                Gio.BusNameWatcherFlags.NONE,
                (connection, name, nameOwner) => {
                    console.log(`[VoiceAssistant] Demone apparso sul bus (${nameOwner})`);
                    new VoiceAssistantProxy(
                        Gio.DBus.session,
                        'org.local.VoiceAssistant',
                        '/org/local/VoiceAssistant',
                        (proxy, error) => {
                            if (error) {
                                console.error(`[VoiceAssistant] Errore di connessione D-Bus: ${error.message}`);
                                return;
                            }
                            this._dbusProxy = proxy;

                            this._stateSignalId = this._dbusProxy.connectSignal(
                                'StateChanged',
                                (proxy, senderName, [newState]) => {
                                    this._updateUiState(newState);
                                }
                            );
                            
                            this._progressSignal = this._dbusProxy.connectSignal('DownloadProgress',
                                (proxy, senderName, [pName, mName, percent]) => {
                                    if (this._downloadItem) {
                                        if (percent >= 0 && percent < 100) {
                                            this._downloadItem.label.text = _(`Scaricamento ${pName} (${mName}): ${percent}%`);
                                            this._downloadItem.visible = true;
                                        } else {
                                            this._downloadItem.visible = false;
                                        }
                                    }
                                });
                        }
                    );
                },
                () => {
                    console.log('[VoiceAssistant] Demone scomparso dal bus');
                    this._dbusProxy = null;
                }
            );
        }

        _toggleRecording() {
            if (this._dbusProxy) {
                this._dbusProxy.ToggleListeningRemote((result, error) => {
                    if (error) {
                        console.error(`[VoiceAssistant] Errore ToggleListening: ${error.message}`);
                    }
                });
            } else {
                let currentState = this._settings.get_boolean('enabled');
                this._settings.set_boolean('enabled', !currentState);
            }
        }

        _updateUiState(state) {
            console.log(`[VoiceAssistant] Aggiornamento stato UI: ${state}`);
            switch (state) {
                case 'listening':
                    this._icon.icon_name = null;
                    this._icon.gicon = this._customGIcon;
                    this._icon.set_style('color: #e01b24;'); 
                    if (this._toggleItem) this._toggleItem.label.text = _('Disattiva Assistente');
                    break;
                case 'processing':
                    this._icon.gicon = null;
                    this._icon.icon_name = 'brain-augmented-symbolic';
                    this._icon.set_style('color: #e5a50a;');
                    if (this._toggleItem) this._toggleItem.label.text = _('Disattiva Assistente');
                    break;
                case 'speaking':
                    this._icon.gicon = null;
                    this._icon.icon_name = 'audio-volume-high-symbolic';
                    this._icon.set_style('color: #3584e4;');
                    if (this._toggleItem) this._toggleItem.label.text = _('Disattiva Assistente');
                    break;
                case 'downloading':
                    this._icon.gicon = null;
                    this._icon.icon_name = 'folder-download-symbolic';
                    this._icon.set_style('color: #e5a50a;');
                    break;
                case 'disabled':
                    this._icon.icon_name = null;
                    this._icon.gicon = this._customGIcon;
                    this._icon.set_style('color: #e01b24;'); 
                    if (this._toggleItem) this._toggleItem.label.text = _('Attiva Assistente');
                    break;
                case 'idle':
                default:
                    this._icon.icon_name = null;
                    this._icon.gicon = this._customGIcon;
                    this._icon.set_style(null);
                    if (this._toggleItem) this._toggleItem.label.text = _('Disattiva Assistente');
                    if (this._downloadItem) this._downloadItem.visible = false;
                    break;
            }
        }

        destroy() {
            if (this._watchId) {
                Gio.bus_unwatch_name(this._watchId);
                this._watchId = 0;
            }
            if (this._dbusProxy) {
                if (this._stateSignalId) this._dbusProxy.disconnectSignal(this._stateSignalId);
                if (this._progressSignal) this._dbusProxy.disconnectSignal(this._progressSignal);
                this._stateSignalId = null;
                this._progressSignal = null;
                this._dbusProxy = null;
            }
            super.destroy();
        }
    });

// Classe principale dell'estensione
export default class VoiceAssistantExtension extends Extension {
    enable() {
        // Carica e registra il bundle GResource
        this._resource = Gio.Resource.load(this.dir.get_child('org.gnome.shell.extensions.voice-assistant.gresource').get_path());
        Gio.resources_register(this._resource);

        this._indicator = new AssistantIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);

        // Scorciatoia da tastiera nativa per attivare/disattivare l'ascolto
        this._settings = this.getSettings();
        Main.wm.addKeybinding(
            'toggle-shortcut',
            this._settings,
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            () => {
                if (this._indicator) {
                    this._indicator._toggleRecording();
                }
            }
        );

        // Installa e avvia il demone tramite Systemd/DBus
        setupDaemonServices(this.dir);
    }

    disable() {
        Main.wm.removeKeybinding('toggle-shortcut');

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }

        if (this._resource) {
            Gio.resources_unregister(this._resource);
            this._resource = null;
        }
    }
}