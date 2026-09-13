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

    // 3. Install Desktop Application Entry (per far comparire l'icona nel menu Applicazioni di GNOME)
    let appsDir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_data_dir(), 'applications']));
    if (!appsDir.query_exists(null)) {
        appsDir.make_directory_with_parents(null);
    }

    try {
        let desktopContent = loadTemplate('/org/gnome/shell/extensions/voice-assistant/services/org.local.VoiceAssistant.desktop.in', 'org.local.VoiceAssistant.desktop.in');
        let desktopGuiFile = appsDir.get_child('org.local.VoiceAssistant.GUI.desktop');
        desktopGuiFile.replace_contents(encoder.encode(desktopContent), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);

        // Also ensure org.local.VoiceAssistant.desktop exists with NoDisplay=true
        // for backwards compatibility and notification routing
        let baseDesktopFile = appsDir.get_child('org.local.VoiceAssistant.desktop');
        let baseContent = desktopContent;
        if (!baseContent.includes('NoDisplay=')) {
            baseContent += '\nNoDisplay=true\n';
        } else {
            baseContent = baseContent.replace(/NoDisplay=false/g, 'NoDisplay=true');
        }
        baseDesktopFile.replace_contents(encoder.encode(baseContent), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    } catch (e) {
        console.error(`[VoiceAssistant] Errore installazione file .desktop: ${e.message}`);
    }

    // 4. Install App Icons in ~/.local/share/icons/hicolor/
    let userIconsDir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_data_dir(), 'icons', 'hicolor']));
    const iconSizes = [
        ['scalable', 'apps', 'vocal-assistant-icon.svg'],
        ['32x32', 'apps', 'vocal-assistant-icon.svg'],
        ['64x64', 'apps', 'vocal-assistant-icon.svg'],
        ['128x128', 'apps', 'vocal-assistant-icon.svg']
    ];

    for (const [size, category, filename] of iconSizes) {
        try {
            let targetDir = userIconsDir.get_child(size).get_child(category);
            if (!targetDir.query_exists(null)) {
                targetDir.make_directory_with_parents(null);
            }
            let targetFile = targetDir.get_child(filename);
            let resourcePath = `/org/gnome/shell/extensions/voice-assistant/icons/hicolor/${size}/${category}/${filename}`;
            let bytes = Gio.resources_lookup_data(resourcePath, Gio.ResourceLookupFlags.NONE);
            targetFile.replace_contents(bytes.get_data(), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        } catch (e) {
            // Ignora se già esistente o errore di scrittura secondario
        }
    }

    // Aggiorna la cache delle icone utente
    try {
        let updateIconCacheProc = new Gio.Subprocess({
            argv: ['gtk-update-icon-cache', '-f', '-t', userIconsDir.get_path()],
            flags: Gio.SubprocessFlags.NONE
        });
        updateIconCacheProc.init(null);
    } catch (e) {
        // Non-bloccante se gtk-update-icon-cache non è presente
    }

    // 5. Reload systemd and start service
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
    } catch (e) {
        console.error(`[VoiceAssistant] systemctl reload failed: ${e.message}`);
    }
}

// Definizione dell'interfaccia D-Bus (Caricata da GResource o fallback completo)
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
    <method name="TriggerListening">
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="ToggleListeningInContext">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="TriggerListeningInContext">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="GetState">
      <arg type="s" direction="out" name="state"/>
    </method>
    <method name="GetAvailableModels">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetInstalledModels">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetDownloadingModels">
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetResourceMetrics">
      <arg type="s" direction="out" name="metrics_json"/>
    </method>
    <method name="DownloadModel">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <method name="CancelDownload">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <method name="DeleteModel">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="GetErrorReports">
      <arg type="s" direction="out" name="reports_json"/>
    </method>
    <method name="ClearErrorReports">
    </method>
    <method name="GenerateDiagnosticBundle">
      <arg type="s" direction="out" name="bundle_path"/>
    </method>
    <method name="GetMarketplaceFeatured">
      <arg type="s" direction="out" name="servers_json"/>
    </method>
    <method name="SearchMarketplace">
      <arg type="s" direction="in" name="query"/>
      <arg type="s" direction="out" name="results_json"/>
    </method>
    <method name="GetServerDetails">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="s" direction="out" name="details_json"/>
    </method>
    <method name="GetMarketplaceCategories">
      <arg type="s" direction="out" name="categories_json"/>
    </method>
    <method name="FilterMarketplaceByCategory">
      <arg type="s" direction="in" name="category"/>
      <arg type="s" direction="out" name="results_json"/>
    </method>
    <method name="InstallMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="s" direction="in" name="server_config_json"/>
      <arg type="s" direction="in" name="env_vars_json"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="StartMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="UninstallMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="TestMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="UpdateServerConfig">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="s" direction="in" name="env_vars_json"/>
      <arg type="b" direction="in" name="enabled"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="GetInstalledServers">
      <arg type="s" direction="out" name="servers_json"/>
    </method>
    <method name="GetSkills">
      <arg type="s" direction="out" name="skills_json"/>
    </method>
    <method name="SaveSkill">
      <arg type="s" direction="in" name="skill_json"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="DeleteSkill">
      <arg type="s" direction="in" name="intent"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="ShowWindow">
    </method>
    <method name="OpenSettings">
    </method>
        <method name="CreateConversation">
      <arg type="s" direction="in" name="title"/>
      <arg type="s" direction="out" name="context_id"/>
    </method>
    <method name="ListConversations">
      <arg type="s" direction="out" name="conversations_json"/>
    </method>
    <method name="DeleteConversation">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="GetConversationMessages">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="s" direction="out" name="messages_json"/>
    </method>
    <method name="ProcessTextInContext">
      <arg type="s" direction="in" name="text"/>
      <arg type="s" direction="in" name="context_id"/>
    </method>
    <method name="ProcessTextInput">
      <arg type="s" direction="in" name="text"/>
    </method>
    <method name="GetMissingDependencies">
      <arg type="s" direction="out" name="deps_json"/>
    </method>
    <method name="GetSpeakerStatus">
      <arg type="s" direction="out" name="status_json"/>
    </method>
    <method name="GetSpeakerProfiles">
      <arg type="s" direction="out" name="profiles_json"/>
    </method>
    <method name="StartSpeakerEnrollment">
      <arg type="s" direction="in" name="display_name"/>
      <arg type="d" direction="in" name="duration_s"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="CancelSpeakerEnrollment">
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="DeleteSpeakerProfile">
      <arg type="s" direction="in" name="profile_id"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <signal name="StateChanged">
      <arg type="s" name="new_state"/>
    </signal>
    <signal name="TranscriptReceived">
      <arg type="s" name="text"/>
      <arg type="b" name="is_final"/>
    </signal>
    <signal name="ResponseTokenStreamed">
      <arg type="s" name="token"/>
      <arg type="b" name="is_complete"/>
    </signal>
    <signal name="DownloadProgress">
      <arg type="s" name="provider"/>
      <arg type="s" name="model"/>
      <arg type="i" name="percent"/>
    </signal>
    <signal name="DependencyRequired">
      <arg type="s" name="package"/>
      <arg type="s" name="description"/>
      <arg type="b" name="is_critical"/>
    </signal>
    <signal name="SpeakerEnrollmentProgress">
      <arg type="d" name="progress"/>
      <arg type="d" name="level"/>
    </signal>
    <signal name="SpeakerEnrollmentFinished">
      <arg type="b" name="success"/>
      <arg type="s" name="profile_id"/>
      <arg type="s" name="message"/>
    </signal>
    <signal name="SpeakerIdentified">
      <arg type="s" name="profile_name"/>
      <arg type="d" name="score"/>
      <arg type="s" name="status"/>
      <arg type="b" name="overlap_detected"/>
    </signal>
    <signal name="SpeakerRejected">
      <arg type="s" name="reason"/>
      <arg type="s" name="message"/>
    </signal>
    <signal name="ConversationCreated">
      <arg type="s" name="context_id"/>
      <arg type="s" name="reason"/>
    </signal>
    <signal name="ConversationToken">
      <arg type="s" name="context_id"/>
      <arg type="s" name="token"/>
      <arg type="b" name="is_complete"/>
    </signal>
    <signal name="ConversationTranscript">
      <arg type="s" name="context_id"/>
      <arg type="s" name="text"/>
      <arg type="b" name="is_final"/>
    </signal>
  </interface>
</node>`;
}

const VoiceAssistantProxy = Gio.DBusProxy.makeProxyWrapper(VoiceAssistantIface);

// Helper per il caricamento garantito delle icone SVG dall'estensione
function getIcon(extension, name) {
    if (extension && extension.path) {
        let iconPath = `${extension.path}/icons/${name}.svg`;
        if (GLib.file_test(iconPath, GLib.FileTest.EXISTS)) {
            return Gio.FileIcon.new(Gio.File.new_for_path(iconPath));
        }
        let hicolorPath = `${extension.path}/icons/hicolor/scalable/status/${name}.svg`;
        if (GLib.file_test(hicolorPath, GLib.FileTest.EXISTS)) {
            return Gio.FileIcon.new(Gio.File.new_for_path(hicolorPath));
        }
    }
    try {
        let resourcePath = `/org/gnome/shell/extensions/voice-assistant/icons/${name}.svg`;
        let bytes = Gio.resources_lookup_data(resourcePath, Gio.ResourceLookupFlags.NONE);
        if (bytes) {
            return Gio.icon_new_for_string(`resource://${resourcePath}`);
        }
    } catch (e) {
        // Fallback su themed icon se la risorsa non è ancora registrata
    }
    return Gio.ThemedIcon.new(name);
}

// Quick Settings Toggle Button con Menu a tendina
const VoiceAssistantQuickToggle = GObject.registerClass(
    class VoiceAssistantQuickToggle extends QuickSettings.QuickMenuToggle {
        _init(extension) {
            let toggleIcon = getIcon(extension, 'vocal-assistant-symbolic');
            super._init({
                title: _('Voice Assistant'),
                subtitle: _('In attesa'),
                gicon: toggleIcon,
                toggleMode: true,
            });

            this._extension = extension;

            this.menu.setHeader(toggleIcon, _('Voice Assistant'), null);

            this.connect('clicked', () => {
                let isEnabled = this._extension._settings.get_boolean('enabled');
                this._extension._settings.set_boolean('enabled', !isEnabled);
            });

            // Voce per avviare l'ascolto vocale immediato
            this._listenItem = new PopupMenu.PopupMenuItem(_('Avvia Ascolto Vocale'));
            this._listenItem.connect('activate', () => {
                if (Main.panel.statusArea?.quickSettings?.menu) {
                    Main.panel.statusArea.quickSettings.menu.close();
                } else if (Main.panel.closeQuickSettings) {
                    Main.panel.closeQuickSettings();
                }
                this._extension._toggleRecording();
            });
            this.menu.addMenuItem(this._listenItem);

            // Voce per aprire la finestra interattiva dell'Assistente
            this._windowItem = new PopupMenu.PopupMenuItem(_('Apri Finestra Assistente'));
            this._windowItem.connect('activate', () => {
                if (Main.panel.statusArea?.quickSettings?.menu) {
                    Main.panel.statusArea.quickSettings.menu.close();
                } else if (Main.panel.closeQuickSettings) {
                    Main.panel.closeQuickSettings();
                }
                this._extension._openAssistantWindow();
            });
            this.menu.addMenuItem(this._windowItem);

            // Separatore prima di Preferenze
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

            // Voce per le preferenze / impostazioni
            let settingsItem = new PopupMenu.PopupMenuItem(_('Preferenze'));
            settingsItem.connect('activate', () => {
                if (Main.panel.statusArea?.quickSettings?.menu) {
                    Main.panel.statusArea.quickSettings.menu.close();
                } else if (Main.panel.closeQuickSettings) {
                    Main.panel.closeQuickSettings();
                }
                this._extension.openPreferences();
            });
            this.menu.addMenuItem(settingsItem);

            // Inizializza l'oggetto _settingsActions per agganciare le preferenze
            if (!this.menu._settingsActions) {
                this.menu._settingsActions = {};
            }

            // Ensure the settings are unavailable when the screen is locked
            settingsItem.visible = Main.sessionMode.allowSettings;
            this.menu._settingsActions[this._extension.uuid] = settingsItem;

            // Aggiorna la visibilità del menu Preferenze ai cambi di sessione (es. blocco/sblocco)
            this._sessionUpdatedId = Main.sessionMode.connect('updated', () => {
                settingsItem.visible = Main.sessionMode.allowSettings;
            });
        }

        updateUiState(state) {
            let isEnabled = this._extension._settings ? this._extension._settings.get_boolean('enabled') : true;
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
                    this.checked = isEnabled;
                    this.subtitle = isEnabled ? _('In attesa') : _('Disabilitato');
                    break;
            }
        }

        destroy() {
            if (this._sessionUpdatedId) {
                Main.sessionMode.disconnect(this._sessionUpdatedId);
                this._sessionUpdatedId = null;
            }
            if (this.menu._settingsActions && this.menu._settingsActions[this._extension.uuid]) {
                delete this.menu._settingsActions[this._extension.uuid];
            }
            super.destroy();
        }
    });

// Quick Settings System Indicator (Gestisce sia l'icona in Top Bar che il pulsante Toggle)
const VoiceAssistantSystemIndicator = GObject.registerClass(
    class VoiceAssistantSystemIndicator extends QuickSettings.SystemIndicator {
        _init(extension) {
            super._init();
            this._extension = extension;

            this._customGIcon = getIcon(extension, 'vocal-assistant-symbolic');
            this._downloadIcon = getIcon(extension, 'folder-download-symbolic');

            // 1. Aggiunge l'icona dell'assistente direttamente all'area di stato di sistema
            this._indicator = this._addIndicator();
            this._indicator.gicon = this._customGIcon;
            this._indicator.style_class = 'system-status-icon voice-assistant-indicator';

            // 2. Crea e aggiunge il pulsante Toggle (QuickMenuToggle) ai menu
            this.quickToggle = new VoiceAssistantQuickToggle(extension);
            this.quickSettingsItems.push(this.quickToggle);
        }

        updateUiState(state) {
            // Aggiorna lo stato del Toggle nel menu a tendina
            if (this.quickToggle) {
                this.quickToggle.updateUiState(state);
            }

            // Aggiorna lo stato dell'icona nella barra superiore
            if (!this._indicator) return;

            if (state === 'disabled') {
                this._indicator.visible = false;
                return;
            }
            this._indicator.visible = true;
            this._indicator.icon_name = null;

            switch (state) {
                case 'listening':
                    this._indicator.gicon = this._customGIcon;
                    this._indicator.set_style('color: #3584e4;'); // Blu GNOME
                    break;
                case 'processing':
                    this._indicator.gicon = this._customGIcon;
                    this._indicator.set_style('color: #e5a50a;'); // Giallo/Arancio GNOME
                    break;
                case 'speaking':
                    this._indicator.gicon = this._customGIcon;
                    this._indicator.set_style('color: #2ec27e;'); // Verde GNOME
                    break;
                case 'downloading':
                    this._indicator.gicon = this._downloadIcon;
                    this._indicator.set_style('color: #e5a50a;');
                    break;
                case 'unavailable':
                    this._indicator.gicon = this._customGIcon;
                    this._indicator.set_style('color: #e01b24;'); // Rosso GNOME
                    break;
                case 'idle':
                default:
                    this._indicator.gicon = this._customGIcon;
                    this._indicator.set_style(null);
                    break;
            }
        }

        destroy() {
            if (this.quickToggle) {
                this.quickToggle.destroy();
                this.quickToggle = null;
            }
            this._indicator = null;
            super.destroy();
        }
    });

// Classe principale dell'estensione
export default class VoiceAssistantExtension extends Extension {
    enable() {
        this._resource = Gio.Resource.load(this.dir.get_child('org.gnome.shell.extensions.voice-assistant.gresource').get_path());
        Gio.resources_register(this._resource);

        try {
            const display = Gdk.Display.get_default();
            if (display) {
                const iconTheme = Gtk.IconTheme.get_for_display(display);
                iconTheme.add_resource_path('/org/gnome/shell/extensions/voice-assistant/icons');
                iconTheme.add_resource_path('/org/gnome/shell/extensions/voice-assistant/icons/hicolor');
                const iconsDir = this.dir.get_child('icons').get_path();
                if (iconsDir && GLib.file_test(iconsDir, GLib.FileTest.EXISTS)) {
                    iconTheme.add_search_path(iconsDir);
                    iconTheme.add_search_path(`${iconsDir}/hicolor`);
                }
            }
        } catch (e) {
            console.warn('[VoiceAssistant] Errore registrazione IconTheme:', e);
        }

        this._settings = this.getSettings('org.gnome.shell.extensions.voice-assistant');
        this._lastState = 'unavailable';
        this._dbusProxy = null;

        this._connectToDaemon();
        this._syncIndicators();

        this._settingsSignal = this._settings.connect('changed::enabled', () => {
            let isEnabled = this._settings.get_boolean('enabled');
            if (!this._dbusProxy) {
                this._updateUiState(isEnabled ? 'idle' : 'disabled');
            }
        });

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
                        if (this._dbusProxy) {
                            if (this._stateSignalId) {
                                try { this._dbusProxy.disconnectSignal(this._stateSignalId); } catch (e) { }
                                this._stateSignalId = null;
                            }
                            if (this._progressSignal) {
                                try { this._dbusProxy.disconnectSignal(this._progressSignal); } catch (e) { }
                                this._progressSignal = null;
                            }
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
                                this._updateDownloadProgress(pName, mName, percent);
                            });

                        if (typeof this._dbusProxy.GetStateRemote === 'function') {
                            this._dbusProxy.GetStateRemote((result, error) => {
                                if (!error && result && result.length > 0) {
                                    this._updateUiState(result[0]);
                                } else {
                                    let isEnabled = this._settings.get_boolean('enabled');
                                    this._updateUiState(isEnabled ? 'idle' : 'disabled');
                                }
                            });
                        } else {
                            let isEnabled = this._settings.get_boolean('enabled');
                            this._updateUiState(isEnabled ? 'idle' : 'disabled');
                        }
                    }
                );
            },
            () => {
                console.log('[VoiceAssistant] Demone scomparso dal bus');
                if (this._dbusProxy) {
                    if (this._stateSignalId) {
                        try { this._dbusProxy.disconnectSignal(this._stateSignalId); } catch (e) { }
                    }
                    if (this._progressSignal) {
                        try { this._dbusProxy.disconnectSignal(this._progressSignal); } catch (e) { }
                    }
                }
                this._stateSignalId = null;
                this._progressSignal = null;
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

        if (this._quickIndicator) {
            this._quickIndicator.updateUiState(state);
        }
    }

    _updateDownloadProgress(pName, mName, percent) {
        if (this._quickIndicator && this._quickIndicator.quickToggle) {
            if (percent >= 0 && percent < 100) {
                this._quickIndicator.quickToggle.subtitle = _(`Download ${pName} (${mName}): ${percent}%`);
            } else {
                this._quickIndicator.updateUiState(this._lastState);
            }
        }
    }

    _launchGuiDirect(args = []) {
        try {
            let startScript = null;
            if (this.path) {
                let candidate = GLib.build_filenamev([this.path, 'gui', 'start.sh']);
                if (GLib.file_test(candidate, GLib.FileTest.EXISTS)) {
                    startScript = candidate;
                }
            }
            if (!startScript && this.dir) {
                let file = this.dir.get_child('gui').get_child('start.sh');
                if (file.query_exists(null)) {
                    startScript = file.get_path();
                }
            }

            if (startScript) {
                Gio.Subprocess.new(
                    ['bash', startScript, ...args],
                    Gio.SubprocessFlags.NONE
                );
                console.log('[VoiceAssistant] Finestra GUI avviata direttamente tramite start.sh');
            } else {
                console.warn('[VoiceAssistant] Script GUI start.sh non trovato');
            }
        } catch (e) {
            console.error('[VoiceAssistant] Errore avvio GUI diretto:', e.message);
        }
    }

    _openAssistantWindow() {
        this._launchGuiDirect();
    }

    disable() {
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }

        if (this._dbusProxy) {
            if (this._stateSignalId) {
                try { this._dbusProxy.disconnectSignal(this._stateSignalId); } catch (e) { }
            }
            if (this._progressSignal) {
                try { this._dbusProxy.disconnectSignal(this._progressSignal); } catch (e) { }
            }
            this._stateSignalId = null;
            this._progressSignal = null;
            this._dbusProxy = null;
        }

        if (this._settings && this._settingsSignal) {
            try { this._settings.disconnect(this._settingsSignal); } catch (e) { }
            this._settingsSignal = null;
        }

        Main.wm.removeKeybinding('toggle-shortcut');

        if (this._quickIndicator) {
            this._quickIndicator.destroy();
            this._quickIndicator = null;
        }

        if (this._resource) {
            Gio.resources_unregister(this._resource);
            this._resource = null;
        }
    }
}