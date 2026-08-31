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
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';

// Funzione per l'installazione dei servizi systemd e dbus (stile GSConnect)
function setupDaemonServices(extensionDir) {
    let startScript = extensionDir.get_child('daemon').get_child('start.sh').get_path();
    let encoder = new TextEncoder();
    let decoder = new TextDecoder();
    let servicesDir = extensionDir.get_child('services');

    // Funzione helper per caricare i template .in da GResource o da disco
    const loadTemplate = (resourcePath, diskFileName) => {
        try {
            let bytes = Gio.resources_lookup_data(resourcePath, Gio.ResourceLookupFlags.NONE);
            return decoder.decode(bytes.get_data());
        } catch (e) {
            let tplFile = servicesDir.get_child(diskFileName);
            if (tplFile.query_exists(null)) {
                let [, bytes] = tplFile.load_contents(null);
                return decoder.decode(bytes);
            }
        }
        throw new Error(`Impossibile trovare il template del servizio: ${diskFileName}`);
    };

    // 1. Install Systemd Service
    let systemdDir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_config_dir(), 'systemd', 'user']));
    if (!systemdDir.query_exists(null)) {
        systemdDir.make_directory_with_parents(null);
    }
    
    try {
        let systemdTpl = loadTemplate('/org/gnome/shell/extensions/voice-assistant/services/voice-assistant.service.in', 'voice-assistant.service.in');
        let systemdContent = systemdTpl.replace(/@startScript@/g, startScript);
        let systemdService = systemdDir.get_child('voice-assistant.service');
        systemdService.replace_contents(encoder.encode(systemdContent), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    } catch (e) {
        console.error(`[VoiceAssistant] Errore installazione servizio systemd: ${e.message}`);
    }

    // 2. Install DBus Service
    let dbusDir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_data_dir(), 'dbus-1', 'services']));
    if (!dbusDir.query_exists(null)) {
        dbusDir.make_directory_with_parents(null);
    }
    
    try {
        let dbusTpl = loadTemplate('/org/gnome/shell/extensions/voice-assistant/services/org.local.VoiceAssistant.service.in', 'org.local.VoiceAssistant.service.in');
        let dbusContent = dbusTpl.replace(/@startScript@/g, startScript);
        let dbusService = dbusDir.get_child('org.local.VoiceAssistant.service');
        dbusService.replace_contents(encoder.encode(dbusContent), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    } catch (e) {
        console.error(`[VoiceAssistant] Errore installazione servizio D-Bus: ${e.message}`);
    }

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

// Quick Settings Toggle Button con Menu a tendina
const VoiceAssistantQuickToggle = GObject.registerClass(
class VoiceAssistantQuickToggle extends QuickSettings.QuickMenuToggle {
    _init(extension) {
        super._init({
            title: _('Voice Assistant'),
            subtitle: _('In attesa'),
            gicon: Gio.icon_new_for_string('resource:///org/gnome/shell/extensions/voice-assistant/icons/vocal-assistant-symbolic.svg'),
            toggleMode: true,
        });

        this._extension = extension;

        this.connect('clicked', () => {
            this._extension._toggleRecording();
        });

        // Header del Menu QuickSettings
        this.menu.setHeader('resource:///org/gnome/shell/extensions/voice-assistant/icons/vocal-assistant-symbolic.svg', _('Voice Assistant'), _('Assistente Vocale Locale'));

        // Voce unicamente per le preferenze / impostazioni
        this._settingsItem = new PopupMenu.PopupMenuItem(_('Preferences'));
        this._settingsItem.connect('activate', () => {
            if (Main.panel.closeQuickSettings) {
                Main.panel.closeQuickSettings();
            }
            this._extension.openPreferences();
        });
        this.menu.addMenuItem(this._settingsItem);
    }

    updateUiState(state) {
        switch (state) {
            case 'listening':
                this.checked = true;
                this.subtitle = _('In ascolto...');
                break;
            case 'processing':
                this.checked = true;
                this.subtitle = _('Elaborazione...');
                break;
            case 'speaking':
                this.checked = true;
                this.subtitle = _('Riproduzione...');
                break;
            case 'downloading':
                this.checked = true;
                this.subtitle = _('Download...');
                break;
            case 'disabled':
                this.checked = false;
                this.subtitle = _('Disabilitato');
                break;
            case 'unavailable':
                this.checked = false;
                this.subtitle = _('Non disponibile');
                break;
            case 'idle':
            default:
                this.checked = true;
                this.subtitle = _('In attesa');
                break;
        }
    }
});

// Quick Settings System Indicator
const VoiceAssistantSystemIndicator = GObject.registerClass(
class VoiceAssistantSystemIndicator extends QuickSettings.SystemIndicator {
    _init(extension) {
        super._init();
        this._extension = extension;
        this._toggle = new VoiceAssistantQuickToggle(extension);
        this.quickSettingsItems.push(this._toggle);
    }

    updateUiState(state) {
        if (this._toggle) {
            this._toggle.updateUiState(state);
        }
    }

    destroy() {
        if (this._toggle) {
            this._toggle.destroy();
            this._toggle = null;
        }
        super.destroy();
    }
});

// Pulsante nella Top Bar (trigger diretto al click, senza menu a tendina)
const AssistantIndicator = GObject.registerClass(
    class AssistantIndicator extends PanelMenu.Button {
        _init(extension) {
            super._init(0.5, 'Voice Assistant Trigger', true);
            this._extension = extension;

            this._customGIcon = Gio.icon_new_for_string('resource:///org/gnome/shell/extensions/voice-assistant/icons/vocal-assistant-symbolic.svg');
            this._icon = new St.Icon({
                gicon: this._customGIcon,
                style_class: 'system-status-icon voice-assistant-indicator',
            });
            this.add_child(this._icon);

            this.connect('event', (actor, event) => {
                if (event.type() === Clutter.EventType.BUTTON_PRESS) {
                    this._extension._toggleRecording();
                    return Clutter.EVENT_STOP;
                }
                return Clutter.EVENT_PROPAGATE;
            });

            this._updateUiState(this._extension._lastState || 'unavailable');
        }

        _updateUiState(state) {
            // L'icona nella barra in alto appare SOLO quando l'assistente è attivo (non disabled o unavailable)
            if (state === 'disabled' || state === 'unavailable') {
                this.visible = false;
                return;
            }
            this.visible = true;

            switch (state) {
                case 'listening':
                    this._icon.icon_name = null;
                    this._icon.gicon = this._customGIcon;
                    this._icon.set_style('color: #3584e4;');
                    break;
                case 'processing':
                    this._icon.gicon = null;
                    this._icon.icon_name = 'brain-augmented-symbolic';
                    this._icon.set_style('color: #e5a50a;');
                    break;
                case 'speaking':
                    this._icon.gicon = null;
                    this._icon.icon_name = 'audio-volume-high-symbolic';
                    this._icon.set_style('color: #3584e4;');
                    break;
                case 'downloading':
                    this._icon.gicon = null;
                    this._icon.icon_name = 'folder-download-symbolic';
                    this._icon.set_style('color: #e5a50a;');
                    break;
                case 'idle':
                default:
                    this._icon.icon_name = null;
                    this._icon.gicon = this._customGIcon;
                    this._icon.set_style(null);
                    break;
            }
        }
    }
);

// Classe principale dell'estensione
export default class VoiceAssistantExtension extends Extension {
    enable() {
        this._resource = Gio.Resource.load(this.dir.get_child('org.gnome.shell.extensions.voice-assistant.gresource').get_path());
        Gio.resources_register(this._resource);

        this._settings = this.getSettings('org.gnome.shell.extensions.voice-assistant');
        this._lastState = 'unavailable';
        this._dbusProxy = null;

        this._connectToDaemon();

        this._syncIndicators();

        Main.wm.addKeybinding(
            'toggle-shortcut',
            this._settings,
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            () => {
                this._toggleRecording();
            }
        );

        setupDaemonServices(this.dir);
    }

    _syncIndicators() {
        // Top Panel Indicator (mostrato dinamicamente quando l'assistente è attivo)
        if (!this._indicator) {
            this._indicator = new AssistantIndicator(this);
            Main.panel.addToStatusArea(this.uuid, this._indicator);
        }

        // Quick Settings Indicator (interruttore sempre presente nei Quick Settings)
        if (!this._quickIndicator) {
            this._quickIndicator = new VoiceAssistantSystemIndicator(this);
            Main.panel.statusArea.quickSettings.addExternalIndicator(this._quickIndicator);
        }

        this._updateUiState(this._lastState);
    }

    _connectToDaemon() {
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            'org.local.VoiceAssistant',
            Gio.BusNameWatcherFlags.AUTO_START,
            (connection, name, nameOwner) => {
                console.log(`[VoiceAssistant] Demone apparso sul bus (${nameOwner})`);
                new VoiceAssistantProxy(
                    Gio.DBus.session,
                    'org.local.VoiceAssistant',
                    '/org/local/VoiceAssistant',
                    (proxy, error) => {
                        if (error) {
                            console.error(`[VoiceAssistant] Errore di connessione D-Bus: ${error.message}`);
                            this._updateUiState('unavailable');
                            return;
                        }
                        this._dbusProxy = proxy;
                        let isEnabled = this._settings.get_boolean('enabled');
                        this._updateUiState(isEnabled ? 'idle' : 'disabled');

                        this._stateSignalId = this._dbusProxy.connectSignal(
                            'StateChanged',
                            (proxy, senderName, [newState]) => {
                                this._updateUiState(newState);
                            }
                        );
                        
                        this._progressSignal = this._dbusProxy.connectSignal('DownloadProgress',
                            (proxy, senderName, [pName, mName, percent]) => {
                                this._updateDownloadProgress(pName, mName, percent);
                            });
                    }
                );
            },
            () => {
                console.log('[VoiceAssistant] Demone scomparso dal bus');
                this._dbusProxy = null;
                this._updateUiState('unavailable');
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

    _showOsd(text) {
        try {
            let icon = Gio.icon_new_for_string('resource:///org/gnome/shell/extensions/voice-assistant/icons/vocal-assistant-symbolic.svg');
            if (Main.osdWindowManager.showAll) {
                Main.osdWindowManager.showAll(icon, text, null, null);
            } else {
                Main.osdWindowManager.show(-1, icon, text, null, null);
            }
        } catch (e) {
            console.error(`[VoiceAssistant] Errore OSD: ${e}`);
        }
    }

    _updateUiState(state) {
        this._lastState = state;
        if (state === 'listening') {
            this._showOsd(_('In ascolto...'));
        }

        if (this._indicator) {
            this._indicator._updateUiState(state);
        }
        if (this._quickIndicator) {
            this._quickIndicator.updateUiState(state);
        }
    }

    _updateDownloadProgress(pName, mName, percent) {
        if (this._indicator && this._indicator._downloadItem) {
            if (percent >= 0 && percent < 100) {
                this._indicator._downloadItem.label.text = _(`Downloading ${pName} (${mName}): ${percent}%`);
                this._indicator._downloadItem.visible = true;
            } else {
                this._indicator._downloadItem.visible = false;
            }
        }
    }

    disable() {
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

        Main.wm.removeKeybinding('toggle-shortcut');

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }

        if (this._quickIndicator) {
            if (this._quickIndicator.quickSettingsItems) {
                this._quickIndicator.quickSettingsItems.forEach(item => item.destroy());
            }
            this._quickIndicator.destroy();
            this._quickIndicator = null;
        }

        if (this._resource) {
            Gio.resources_unregister(this._resource);
            this._resource = null;
        }
    }
}