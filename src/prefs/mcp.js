import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';

const DBUS_NAME = 'org.local.VoiceAssistant';
const DBUS_PATH = '/org/local/VoiceAssistant';
const DBUS_INTERFACE = 'org.local.VoiceAssistant';

export function setupMcpPage(builder, settings, window) {
    const mcpPage = builder.get_object('mcp_page');
    if (!mcpPage) {
        return { mcpPage: null };
    }

    const pageBox = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 18,
        margin_top: 18,
        margin_bottom: 18,
        margin_start: 18,
        margin_end: 18,
        vexpand: true,
        hexpand: true,
    });

    const header = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 6,
        css_classes: ['mcp-header-box'],
    });

    const title = new Gtk.Label({
        label: '<span size="larger" weight="bold">MCP marketplace</span>',
        use_markup: true,
        xalign: 0,
    });
    const subtitle = new Gtk.Label({
        label: 'Scopri, installa e gestisci tool e servizi esterni per il tuo assistente.',
        xalign: 0,
        css_classes: ['dim-label'],
        wrap: true,
    });
    header.append(title);
    header.append(subtitle);
    pageBox.append(header);

    pageBox.append(_createMarketplaceView());
    pageBox.append(_createInstalledServersView());
    pageBox.append(_createConfigView(settings));

    const mcpGroup = new Adw.PreferencesGroup();
    const contentRow = new Adw.PreferencesRow({
        activatable: false,
    });
    contentRow.set_child(pageBox);
    mcpGroup.add(contentRow);
    mcpPage.add(mcpGroup);
    return { mcpPage };
}

function _createMarketplaceView() {
    const box = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 12,
        css_classes: ['mcp-section'],
        vexpand: true,
    });

    const headerRow = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 8,
        margin_top: 4,
        margin_start: 4,
        margin_end: 4,
        halign: Gtk.Align.FILL,
    });

    const title = new Gtk.Label({
        label: '<span weight="bold">Marketplace</span>',
        use_markup: true,
        xalign: 0,
        hexpand: true,
    });
    const badge = new Gtk.Label({
        label: 'Featured',
        css_classes: ['mcp-badge'],
    });
    headerRow.append(title);
    headerRow.append(badge);
    box.append(headerRow);

    const searchBox = new Gtk.SearchEntry({
        placeholder_text: 'Cerca server MCP...',
        css_classes: ['mcp-search'],
    });
    box.append(searchBox);

    const scrolledWindow = new Gtk.ScrolledWindow({
        hexpand: true,
        vexpand: true,
        min_content_height: 260,
    });

    const flowBox = new Gtk.FlowBox({
        selection_mode: Gtk.SelectionMode.NONE,
        homogeneous: false,
        max_children_per_line: 2,
        row_spacing: 12,
        column_spacing: 12,
        margin_top: 4,
        margin_bottom: 4,
        margin_start: 4,
        margin_end: 4,
        valign: Gtk.Align.START,
    });

    scrolledWindow.set_child(flowBox);
    box.append(scrolledWindow);

    _loadMarketplaceServers(flowBox, searchBox);
    return box;
}

function _createInstalledServersView() {
    const box = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 12,
        css_classes: ['mcp-section'],
    });

    const headerRow = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 8,
        margin_top: 4,
        margin_start: 4,
        margin_end: 4,
    });

    const title = new Gtk.Label({
        label: '<span weight="bold">Server installati</span>',
        use_markup: true,
        xalign: 0,
        hexpand: true,
    });
    const badge = new Gtk.Label({
        label: 'Active',
        css_classes: ['mcp-badge'],
    });
    headerRow.append(title);
    headerRow.append(badge);
    box.append(headerRow);

    const scrolledWindow = new Gtk.ScrolledWindow({
        hexpand: true,
        vexpand: true,
        min_content_height: 220,
    });

    const listBox = new Gtk.ListBox({
        selection_mode: Gtk.SelectionMode.NONE,
        css_classes: ['boxed-list'],
    });

    scrolledWindow.set_child(listBox);
    box.append(scrolledWindow);

    _loadInstalledServers(listBox);
    return box;
}

function _createConfigView(settings) {
    const box = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 12,
        css_classes: ['mcp-section'],
    });

    const headerRow = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 8,
        margin_top: 4,
        margin_start: 4,
        margin_end: 4,
    });

    const title = new Gtk.Label({
        label: '<span weight="bold">Configurazione</span>',
        use_markup: true,
        xalign: 0,
        hexpand: true,
    });
    const badge = new Gtk.Label({
        label: 'Advanced',
        css_classes: ['mcp-badge'],
    });
    headerRow.append(title);
    headerRow.append(badge);
    box.append(headerRow);

    const toggleRow = new Adw.ActionRow({
        title: 'Abilita integrazione MCP',
        subtitle: 'Attiva o disattiva i tool esterni e la loro esposizione al modello.',
    });
    const mcpToggle = new Gtk.Switch({
        valign: Gtk.Align.CENTER,
    });
    settings.bind('mcp-enabled', mcpToggle, 'active', Gio.SettingsBindFlags.DEFAULT);
    toggleRow.add_suffix(mcpToggle);
    box.append(toggleRow);

    const registryRow = new Adw.EntryRow({
        title: 'URL registry marketplace',
    });
    settings.bind('mcp-registry-url', registryRow, 'text', Gio.SettingsBindFlags.DEFAULT);
    box.append(registryRow);

    const metricsRow = new Adw.ActionRow({
        title: 'Risorse modelli',
        subtitle: 'Caricamento metriche runtime...',
    });
    const refreshMetricsButton = new Gtk.Button({
        icon_name: 'view-refresh-symbolic',
        valign: Gtk.Align.CENTER,
        tooltip_text: 'Aggiorna metriche risorse',
    });
    refreshMetricsButton.connect('clicked', () => _loadResourceMetrics(metricsRow));
    metricsRow.add_suffix(refreshMetricsButton);
    box.append(metricsRow);
    _loadResourceMetrics(metricsRow);

    const infoBox = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 8,
        margin_top: 8,
        css_classes: ['mcp-info-box'],
    });

    const infoLabel = new Gtk.Label({
        label: '📝 I server MCP aggiungono tool specializzati al tuo assistente vocale.\n'
            + '🌐 Scopri server da registry online o usali offline.\n'
            + '⚙️ Configura variabili di ambiente (API keys, path, etc.) per server specifici.',
        wrap: true,
        xalign: 0,
        margin_start: 12,
        margin_end: 12,
        margin_top: 12,
        margin_bottom: 12,
    });
    infoBox.append(infoLabel);
    box.append(infoBox);

    return box;
}

async function _loadMarketplaceServers(flowBox, searchEntry) {
    try {
        const proxy = await _getDMCPProxy();
        
        // Load featured
        const featured = await proxy.get_marketplace_featured();
        let servers = JSON.parse(featured);
        
        _renderServerCards(flowBox, servers);
        
        // Handle search
        searchEntry.connect('search-changed', async () => {
            const query = searchEntry.get_text();
            if (query.length < 2) {
                const featured = await proxy.get_marketplace_featured();
                servers = JSON.parse(featured);
            } else {
                const results = await proxy.search_marketplace(query);
                servers = JSON.parse(results);
            }
            _clearFlowBox(flowBox);
            _renderServerCards(flowBox, servers);
        });
        
    } catch (e) {
        console.error('Errore caricamento marketplace:', e);
    }
}

async function _loadInstalledServers(listBox) {
    try {
        const proxy = await _getDMCPProxy();
        const installed = await proxy.get_installed_servers();
        const servers = JSON.parse(installed);
        
        if (servers.length === 0) {
            const emptyLabel = new Gtk.Label({
                label: 'Nessun server installato',
                css_classes: ['dim-label'],
                margin_top: 24,
            });
            listBox.append(emptyLabel);
            return;
        }
        
        for (const server of servers) {
            const row = new Adw.ActionRow({
                title: server.name,
                subtitle: server.description || server.command,
            });
            
            const toggle = new Gtk.Switch({
                active: server.enabled,
                valign: Gtk.Align.CENTER,
            });
            
            toggle.connect('notify::active', async () => {
                await proxy.update_server_config(
                    server.name,
                    '{}',
                    toggle.get_active(),
                );
            });
            
            row.add_suffix(toggle);
            
            // Add button for config
            const configBtn = new Gtk.Button({
                icon_name: 'document-edit-symbolic',
                valign: Gtk.Align.CENTER,
            });
            configBtn.connect('clicked', () => {
                _showServerConfigDialog(server);
            });
            row.add_suffix(configBtn);
            
            // Add button for uninstall
            const uninstallBtn = new Gtk.Button({
                icon_name: 'edit-delete-symbolic',
                valign: Gtk.Align.CENTER,
                css_classes: ['destructive-action'],
            });
            uninstallBtn.connect('clicked', async () => {
                await proxy.uninstall_mcp_server(server.name);
                listBox.remove(row);
            });
            row.add_suffix(uninstallBtn);
            
            listBox.append(row);
        }
        
    } catch (e) {
        console.error('Errore caricamento server installati:', e);
    }
}

function _renderServerCards(flowBox, servers) {
    for (const server of servers) {
        const card = _createServerCard(server);
        flowBox.append(card);
    }
}

function _createServerCard(server) {
    const card = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        css_classes: ['mcp-card'],
        margin_start: 4,
        margin_end: 4,
        margin_top: 4,
        margin_bottom: 4,
        width_request: 260,
    });

    const headerBox = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 8,
        margin_start: 12,
        margin_top: 12,
        margin_end: 12,
        valign: Gtk.Align.START,
    });

    const titleLabel = new Gtk.Label({
        label: `<span weight="bold">${server.title || server.name}</span>`,
        use_markup: true,
        xalign: 0,
        hexpand: true,
        wrap: true,
        css_classes: ['mcp-card-title'],
    });
    const badge = new Gtk.Label({
        label: (server.category || 'Altro').toUpperCase(),
        css_classes: ['mcp-status-pill'],
    });
    headerBox.append(titleLabel);
    headerBox.append(badge);
    card.append(headerBox);

    const descLabel = new Gtk.Label({
        label: server.description || 'Nessuna descrizione disponibile.',
        xalign: 0,
        margin_start: 12,
        margin_top: 8,
        margin_end: 12,
        wrap: true,
        css_classes: ['dim-label'],
    });
    card.append(descLabel);

    const metaRow = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 8,
        margin_start: 12,
        margin_top: 8,
        margin_end: 12,
        margin_bottom: 12,
        valign: Gtk.Align.END,
    });

    const statusLabel = new Gtk.Label({
        label: server.installed ? '✓ Installato' : 'Disponibile',
        css_classes: server.installed ? ['mcp-installed-pill'] : ['mcp-available-pill'],
    });
    metaRow.append(statusLabel);

    const actionBox = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 6,
        hexpand: true,
        halign: Gtk.Align.END,
    });

    if (server.installed) {
        const installedLabel = new Gtk.Label({
            label: 'Pronto',
            css_classes: ['mcp-installed-pill'],
        });
        actionBox.append(installedLabel);
    } else {
        const installBtn = new Gtk.Button({
            label: 'Installa',
            css_classes: ['suggested-action', 'mcp-primary-button'],
        });
        installBtn.connect('clicked', () => {
            _showInstallDialog(server);
        });
        actionBox.append(installBtn);
    }

    metaRow.append(actionBox);
    card.append(metaRow);
    return card;
}

function _showInstallDialog(server) {
    const dialog = new Adw.Dialog();
    const headerBar = new Adw.HeaderBar();
    
    const box = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        margin_top: 12,
        margin_bottom: 12,
        margin_start: 12,
        margin_end: 12,
        spacing: 12,
    });
    
    // Server info
    const infoLabel = new Gtk.Label({
        label: `<b>${server.title}</b>\n${server.description}`,
        use_markup: true,
        wrap: true,
        xalign: 0,
    });
    box.append(infoLabel);
    
    // Command preview
    const cmdLabel = new Gtk.Label({
        label: `<small><tt>${server.command} ${server.args.join(' ')}</tt></small>`,
        use_markup: true,
        xalign: 0,
        css_classes: ['monospace', 'dim-label'],
    });
    box.append(cmdLabel);
    
    // Environment variables
    if (Object.keys(server.env).length > 0) {
        const envLabel = new Gtk.Label({
            label: '<b>Variabili di Ambiente Richieste:</b>',
            use_markup: true,
            xalign: 0,
        });
        box.append(envLabel);
        
        const envEntries = {};
        for (const [key, _] of Object.entries(server.env)) {
            const row = new Adw.EntryRow({
                title: key,
                placeholder_text: `Inserisci ${key}`,
            });
            envEntries[key] = row;
            box.append(row);
        }
    }
    
    // Buttons
    const buttonBox = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 6,
        margin_top: 12,
    });
    
    const cancelBtn = new Gtk.Button({
        label: 'Annulla',
    });
    buttonBox.append(cancelBtn);
    
    const installBtn = new Gtk.Button({
        label: 'Installa',
        css_classes: ['suggested-action'],
    });
    installBtn.set_hexpand(true);
    buttonBox.append(installBtn);
    
    box.append(buttonBox);
    
    const scrolled = new Gtk.ScrolledWindow({
        child: box,
        hexpand: true,
        vexpand: true,
    });
    
    dialog.set_child(scrolled);
    
    // Install handler
    installBtn.connect('clicked', async () => {
        try {
            const proxy = await _getDMCPProxy();
            const success = await proxy.install_mcp_server(
                server.name,
                JSON.stringify(server),
                '{}',
            );
            if (success[0]) {
                dialog.force_close();
                console.log('Server installato:', success[1]);
            } else {
                console.error('Errore installazione:', success[1]);
            }
        } catch (e) {
            console.error('Errore durante l\'installazione:', e);
        }
    });
    
    cancelBtn.connect('clicked', () => {
        dialog.force_close();
    });
    
    dialog.present();
}

function _showServerConfigDialog(server) {
    const dialog = new Adw.Dialog();
    const box = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL,
        spacing: 12,
        margin_top: 18,
        margin_bottom: 18,
        margin_start: 18,
        margin_end: 18,
        width_request: 420,
    });
    const title = new Gtk.Label({
        label: `<span weight="bold">Configura ${server.name}</span>`,
        use_markup: true,
        xalign: 0,
    });
    const description = new Gtk.Label({
        label: 'I valori gia salvati non vengono mostrati. Lascia vuoto un campo per mantenerne il valore corrente.',
        xalign: 0,
        wrap: true,
        css_classes: ['dim-label'],
    });
    box.append(title);
    box.append(description);

    const entries = {};
    for (const key of server.env_keys || []) {
        const row = new Adw.EntryRow({
            title: key,
            placeholder_text: `Nuovo valore per ${key}`,
        });
        entries[key] = row;
        box.append(row);
    }

    if (Object.keys(entries).length === 0) {
        const empty = new Gtk.Label({
            label: 'Questo server non richiede variabili di ambiente configurate.',
            xalign: 0,
            wrap: true,
            css_classes: ['dim-label'],
        });
        box.append(empty);
    }

    const actions = new Gtk.Box({
        orientation: Gtk.Orientation.HORIZONTAL,
        spacing: 6,
        halign: Gtk.Align.END,
    });
    const cancelButton = new Gtk.Button({ label: 'Annulla' });
    const saveButton = new Gtk.Button({
        label: 'Salva',
        css_classes: ['suggested-action'],
    });
    actions.append(cancelButton);
    actions.append(saveButton);
    box.append(actions);
    dialog.set_child(box);

    cancelButton.connect('clicked', () => dialog.force_close());
    saveButton.connect('clicked', async () => {
        const envUpdates = {};
        for (const [key, entry] of Object.entries(entries)) {
            const value = entry.get_text().trim();
            if (value) {
                envUpdates[key] = value;
            }
        }
        try {
            const proxy = await _getDMCPProxy();
            const result = await proxy.update_server_config(
                server.name,
                JSON.stringify(envUpdates),
                server.enabled,
            );
            if (result[0]) {
                dialog.force_close();
            } else {
                console.error('Errore aggiornamento server:', result[1]);
            }
        } catch (error) {
            console.error('Errore configurazione server:', error);
        }
    });
    dialog.present();
}

async function _getDMCPProxy() {
    try {
        const bus = Gio.DBusConnection.get_sync(Gio.BusType.SESSION, null);
        const proxy = new Gio.DBusProxy({
            connection: bus,
            g_name: DBUS_NAME,
            g_object_path: DBUS_PATH,
            g_interface_name: DBUS_INTERFACE,
        });
        proxy.init_async(GLib.PRIORITY_DEFAULT, null, (proxy, result) => {
            proxy.init_finish(result);
        });
        return proxy;
    } catch (e) {
        console.error('Errore connessione D-Bus:', e);
        throw e;
    }
}

async function _loadResourceMetrics(metricsRow) {
    try {
        const proxy = await _getDMCPProxy();
        const metrics = JSON.parse(await proxy.get_resource_metrics());
        const loaded = Object.entries(metrics.loaded_models || {})
            .filter(([, isLoaded]) => isLoaded)
            .map(([kind]) => kind.toUpperCase())
            .join(', ') || 'Nessun modello pesante caricato';
        const rss = GLib.format_size(metrics.rss_bytes || 0);
        const gpu = metrics.gpu_reserved_bytes > 0
            ? `, GPU ${GLib.format_size(metrics.gpu_reserved_bytes)}`
            : '';

        metricsRow.set_subtitle(`${loaded} · RAM ${rss}${gpu}`);
    } catch (error) {
        console.warn('Errore caricamento metriche risorse:', error);
        metricsRow.set_subtitle('Metriche non disponibili: daemon non connesso');
    }
}

function _clearFlowBox(flowBox) {
    let child = flowBox.get_first_child();
    while (child) {
        const next = child.get_next_sibling();
        flowBox.remove(child);
        child = next;
    }
}
