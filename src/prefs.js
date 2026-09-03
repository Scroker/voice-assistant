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

import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import Gdk from 'gi://Gdk';
import Adw from 'gi://Adw';
import { ExtensionPreferences } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import { setupGeneralPage } from './prefs/general.js';
import { setupSttPage } from './prefs/stt.js';
import { setupLlmPage } from './prefs/llm.js';
import { setupTtsPage } from './prefs/tts.js';
import { setupMcpPage } from './prefs/mcp.js';
import { setupModelSelector } from './prefs/modelSelector.js';

export default class VoiceAssistantPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        // Caricamento e registrazione di GResource ed IconTheme per la finestra delle preferenze
        try {
            const gresourcePath = `${this.path}/org.gnome.shell.extensions.voice-assistant.gresource`;
            const resourceFile = Gio.File.new_for_path(gresourcePath);
            if (resourceFile.query_exists(null)) {
                const resource = Gio.Resource.load(gresourcePath);
                Gio.resources_register(resource);
            }

            const display = window.get_display() || Gdk.Display.get_default();
            if (display) {
                const iconTheme = Gtk.IconTheme.get_for_display(display);
                iconTheme.add_resource_path('/org/gnome/shell/extensions/voice-assistant/icons');
                iconTheme.add_resource_path('/org/gnome/shell/extensions/voice-assistant/icons/hicolor');

                const iconsDir = `${this.path}/icons`;
                if (Gio.File.new_for_path(iconsDir).query_exists(null)) {
                    iconTheme.add_search_path(iconsDir);
                    iconTheme.add_search_path(`${iconsDir}/hicolor`);
                }
            }
        } catch (e) {
            console.warn('[VoiceAssistant] Impossibile registrare le risorse icona in prefs:', e);
        }

        const _ = this.gettext.bind(this);
        const settings = this.getSettings('org.gnome.shell.extensions.voice-assistant');

        // Dimensioni di default per desktop
        window.set_default_size(860, 600);

        // Caricamento interfaccia dal file Blueprint (compilato in prefs.ui nelle risorse)
        const builder = new Gtk.Builder();
        builder.set_translation_domain(this.metadata['gettext-domain'] || 'voice-assistant');
        builder.add_from_resource('/org/gnome/shell/extensions/voice-assistant/ui/prefs.ui');
        const splitView = builder.get_object('split_view');

        // Breakpoint per supporto responsive mobile (< 600px)
        const mobileBreakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 600px')
        );
        mobileBreakpoint.add_setter(splitView, 'collapsed', true);
        window.add_breakpoint(mobileBreakpoint);

        // Sidebar e Gerarchia di Navigazione
        const sidebarListBox = builder.get_object('sidebar_list_box');
        const contentNavigationView = builder.get_object('content_navigation_view');
        const mainContentNavPage = builder.get_object('main_content_nav_page');
        const contentTitle = builder.get_object('content_title');
        const stack = builder.get_object('stack');
        const modelSelectorPage = builder.get_object('model_selector_page');

        // Componente Selettore Modelli
        let refreshCacheGroupFn = null;
        const selector = setupModelSelector(
            builder,
            settings,
            window,
            this.path,
            _,
            () => refreshCacheGroupFn
        );

        // Componente Generale (compreso Storage & Modelli)
        const general = setupGeneralPage(
            builder,
            settings,
            window,
            _,
            () => selector.renderModelList()
        );
        refreshCacheGroupFn = general.refreshCacheGroup;

        // Componente Speech-To-Text (STT)
        setupSttPage(
            builder,
            settings,
            () => selector.openSttSelector(contentNavigationView, splitView),
            _
        );

        // Componente Large Language Model (LLM)
        setupLlmPage(
            builder,
            settings,
            () => selector.openLlmSelector(contentNavigationView, splitView),
            (provider, modelId) => selector.startDownload(provider, modelId),
            _
        );

        // Componente Text-To-Speech (TTS)
        setupTtsPage(builder, settings, _);

        // Componente Model Context Protocol (MCP)
        setupMcpPage(builder, settings, _);

        // Link Documentazione
        const docBtn = builder.get_object('doc_btn');
        if (docBtn) {
            docBtn.connect('clicked', () => {
                Gio.AppInfo.launch_default_for_uri('https://github.com/Scroker/voice-assistant', null);
            });
        }

        const pages = [
            { id: 'general_page', page: builder.get_object('general_page'), title: _('General'), row: builder.get_object('row_general') },
            { id: 'wakeword_page', page: builder.get_object('wakeword_page'), title: _('Wake Word'), row: builder.get_object('row_wakeword') },
            { id: 'stt_page', page: builder.get_object('stt_page'), title: _('Speech Engine (STT)'), row: builder.get_object('row_stt') },
            { id: 'llm_page', page: builder.get_object('llm_page'), title: _('Artificial Intelligence (LLM)'), row: builder.get_object('row_llm') },
            { id: 'tts_page', page: builder.get_object('tts_page'), title: _('Text-to-Speech (TTS)'), row: builder.get_object('row_tts') },
            { id: 'mcp_page', page: builder.get_object('mcp_page'), title: _('Tools (MCP)'), row: builder.get_object('row_mcp') },
            { id: 'models_page', page: builder.get_object('models_page'), title: _('Storage and Models'), row: builder.get_object('row_models') },
            { id: 'about_page', page: builder.get_object('about_page'), title: _('About'), row: builder.get_object('row_about') }
        ];

        const selectSidebarPage = (row) => {
            if (!row) return;
            const target = pages.find(p => p.row === row);
            if (target) {
                if (target.id === 'stt_page') {
                    selector.queryDownloadingModels(() => {
                        selector.renderModelList();
                    });
                }

                if (contentNavigationView.get_visible_page() === modelSelectorPage) {
                    contentNavigationView.pop();
                }

                if (target.page) {
                    stack.set_visible_child(target.page);
                } else {
                    stack.set_visible_child_name(target.id);
                }

                contentTitle.set_title(target.title);
                mainContentNavPage.set_title(target.title);

                splitView.set_show_content(true);
            }
        };

        if (sidebarListBox) {
            sidebarListBox.connect('row-activated', (listbox, row) => selectSidebarPage(row));
            sidebarListBox.connect('row-selected', (listbox, row) => selectSidebarPage(row));

            if (pages[0] && pages[0].row) {
                sidebarListBox.select_row(pages[0].row);
                selectSidebarPage(pages[0].row);
            }
        }

        window.set_content(splitView);
    }
}
