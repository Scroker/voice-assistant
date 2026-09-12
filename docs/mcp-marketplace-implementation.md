# MCP Marketplace & Registry - Implementazione Completa

> [!WARNING]
> **Il titolo è fuorviante: solo il backend è implementato, la UI descritta in questo documento non esiste.** Verificato contro il codice attuale:
> - Il backend (`installer.py`, `registry.py`, i metodi marketplace di `manager.py`) esiste davvero come descritto nella sezione "Backend" sotto.
> - **La UI a 3 tab (Marketplace/Server Installati/Configurazione Avanzata) descritta in "Frontend" non esiste.** `src/gui/components/settings/mcp.py` è una classe `MCPSettings` di 44 righe che si limita a due binding GSettings (`mcp-enabled`, `mcp-registry-url`) e un'unica riga statica popolata da `mcp-servers`. Nessuna search bar, grid, dialog di installazione o tab: `grep -rl "marketplace\|Smithery" src/gui/` non produce risultati.
> - I metodi `GetMarketplaceFeatured`, `SearchMarketplace`, `GetServerDetails` **non sono realmente esposti via D-Bus**: in `main.py` esistono solo come `get_marketplace_featured`, `search_marketplace`, `get_server_details` (snake_case) — dasbus richiede nomi CamelCase per l'esportazione automatica, quindi restano metodi Python interni non raggiungibili dal bus. Vedi [`docs/dbus.md`](dbus.md).
> - `src/prefs/mcp.js` citato nella sezione "File Modificati" **non esiste**: non c'è alcuna directory `src/prefs/` nel repository.
>
> In sintesi: la funzionalità marketplace è un backend pronto ma senza consumer — né la GUI né l'interfaccia D-Bus reale la espongono oggi all'utente finale.

## Architettura Implementata

### Backend (Python - `src/daemon/mcp/`)

#### 1. **installer.py** - Ciclo di Vita Completo
```python
MCPServerInstaller
├── install_server(name, server_def, env_vars)
│   ├── Verifica disponibilità comando (uvx, npx, python, etc)
│   ├── Test di startup con --help
│   ├── Aggiunge server a mcp_servers.json
│   └── Ritorna (success, message)
├── uninstall_server(name)
│   ├── Rimuove da config
│   └── Protegge server built-in
├── test_server(name)
│   └── Verifica connessione funzionante
└── update_server_env(name, env_vars)
    └── Aggiorna variabili ambiente
```

#### 2. **registry.py** - Marketplace Discovery Potenziato
```python
MCPRegistryClient
├── get_featured() → Lista server con stato "installed"
├── search(query) → Ricerca remota + fallback locale
├── get_server_details(name) → Info dettagliate server
├── get_categories() → Lista categorie disponibili
└── filter_by_category(category) → Server per categoria

Features:
- Fetch da https://api.smithery.ai
- Fallback a FEATURED_SERVERS offline
- Stato "installed" calcolato da mcp_servers.json
- Timeout 5s su query remote
```

#### 3. **manager.py** - D-Bus Methods Esposti
```python
MCPManager (potenziato con installer + registry)
├── Marketplace Discovery:
│   ├── get_marketplace_featured() → JSON
│   ├── search_marketplace(query) → JSON
│   ├── get_server_details(name) → JSON
│   ├── get_marketplace_categories() → JSON
│   └── filter_marketplace_by_category(cat) → JSON
├── Installation:
│   ├── install_mcp_server(name, config, env_vars) → (bool, msg)
│   ├── uninstall_mcp_server(name) → (bool, msg)
│   ├── test_mcp_server(name) → (bool, msg)
│   └── update_server_config(name, env_vars, enabled) → (bool, msg)
└── Status:
    └── get_installed_servers() → JSON
```

### Frontend (Python — `src/gui/components/settings/mcp.py`)

> [!NOTE]
> Quanto segue descrive un **design non ancora implementato**. La classe `MCPSettings` reale ha solo ~44 righe: due binding GSettings (`mcp-enabled`, `mcp-registry-url`) e una riga di riepilogo statica dal contenuto di `mcp-servers`. Nessuno degli elementi UI sotto (search bar, grid, dialog, tab) esiste nel codice attuale.

#### UI Structure: Stack di 3 Tab (proposta, non implementata)

**Tab 1: Marketplace**
```
┌─ Search Bar (ricerca in tempo reale)
├─ Server Grid (2 colonne, card-based)
│  ├─ Titolo, descrizione, categoria
│  ├─ Button "Installa" / Badge "✓ Installato"
│  └─ Click → Install Dialog
└─ Install Dialog
   ├─ Anteprima comando
   ├─ Form env vars (se richieste)
   └─ Button Installa (async call)
```

**Tab 2: Server Installati**
```
┌─ ListBox con azioni:
│  ├─ Toggle Enable/Disable
│  ├─ Button Config (edit env vars)
│  └─ Button Uninstall
└─ Empty state se nessuno installato
```

**Tab 3: Configurazione Avanzata**
```
├─ Toggle MCP (enable/disable integrazione)
├─ URL Registry (default: https://api.smithery.ai)
└─ Info panel (documentazione)
```

### D-Bus Integration

> **Nota**: la sezione seguente mostrava in precedenza tutti questi metodi come parte dell'interfaccia D-Bus reale. In realtà, come spiegato nel warning in cima alla pagina, solo le **Installation/Configuration Methods** sono davvero raggiungibili via D-Bus (senza però valori di ritorno, perché i wrapper Python non hanno un'annotazione di tipo — vedi [`docs/dbus.md`](dbus.md)); i tre **Marketplace Methods** sono funzioni Python interne, mai esposte sul bus.

```xml
<!-- Interface esposta al daemon -->
<interface name="org.local.VoiceAssistant">
  <!-- Installation Methods (reali, ma nessun valore di ritorno via D-Bus) -->
  <method name="InstallMCPServer">
    <arg type="s" direction="in" name="server_name"/>
    <arg type="s" direction="in" name="server_config"/>
    <arg type="s" direction="in" name="env_vars"/>
  </method>
  <method name="UninstallMCPServer">
    <arg type="s" direction="in" name="server_name"/>
  </method>
  <method name="TestMCPServer">
    <arg type="s" direction="in" name="server_name"/>
  </method>

  <!-- Configuration Methods -->
  <method name="UpdateServerConfig">
    <arg type="s" direction="in" name="server_name"/>
    <arg type="s" direction="in" name="env_vars"/>
    <arg type="b" direction="in" name="enabled"/>
  </method>
  <method name="GetInstalledServers">
    <arg type="s" direction="out" name="servers_json"/>
  </method>

  <!-- Marketplace "Methods": esistono solo come funzioni Python snake_case
       (get_marketplace_featured, search_marketplace, get_server_details,
       get_marketplace_categories, filter_marketplace_by_category) in main.py.
       Non essendo CamelCase, dasbus non le espone sul bus: NON fanno parte
       dell'interfaccia D-Bus reale. -->
</interface>
```

## Flusso di Installazione Utente

1. **Utente apre Preferences → Tools (MCP)**
2. **Browsing**: 
   - Vede server featured in grid
   - Ricerca "database" → filtra risultati in real-time
   - Clicca su "SQLite Query Tool" → vede dettagli
3. **Install**:
   - Clicca "Installa"
   - Dialog con anteprima comando: `uvx mcp-server-sqlite --db-path ~/.local/share/voice-assistant/database.db`
   - Se richieste env vars (es: `OPENAI_API_KEY`), form per inserirle
   - Clicca "Installa" → backend:
     - Verifica `uvx --version` disponibile
     - Testa `uvx mcp-server-sqlite --help`
     - Aggiunge a `~/.config/voice-assistant/mcp_servers.json`
     - Ritorna "(success, message)"
4. **Management**:
   - Vede server in "Server Installati" tab
   - Toggle Enable → attiva il server al prossimo restart
   - Clicca Config → edita env vars
   - Clicca Uninstall → rimuove da config

## Configurazione (`~/.config/voice-assistant/mcp_servers.json`)

```json
{
  "mcpServers": {
    "gnome-mcp-server": {
      "command": "gnome-mcp-server",
      "args": [],
      "env": {},
      "enabled": true,
      "description": "Native GNOME desktop controls"
    },
    "sqlite-db": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "~/.local/share/voice-assistant/database.db"],
      "env": {},
      "enabled": false,
      "description": "Query local SQLite databases",
      "installed_at": 1725278342.123
    }
  }
}
```

> Il nome legacy `"gnome-system"` con `"command": "builtin"` (usato in una versione precedente di questo esempio) viene automaticamente migrato al valore corrente da `config.py::get_default_config()` al primo avvio; entrambi i nomi restano comunque protetti dalla disinstallazione in `installer.py`.

## Dipendenze Backend

Aggiungi a `src/daemon/mcp/`:
- `urllib` (built-in) - fetch registry remota
- `asyncio` (built-in) - async operations
- `subprocess` (built-in) - verificare comandi disponibili
- `json` (built-in) - parsing config

**Nessuna dipendenza esterna aggiunta** ✅

## Dipendenze Frontend

Già disponibili in GNOME 46+:
- `Gtk` 4.12+
- `Adw` 1.5+ (Libadwaita)
- `Gio` (D-Bus)

## File Modificati / Creati

```
src/daemon/mcp/
├── installer.py              276 righe (non più 160: aggiornato da modifiche successive)
├── registry.py                    (metodi marketplace)
├── manager.py                     (D-Bus methods)
├── config.py
├── credentials.py                 (storage credenziali via keyring, vedi nota sicurezza sotto)
└── client.py
```

> Non esiste alcuna directory `src/prefs/` né un file `mcp.js` nel repository — l'unico file prefs GJS è `src/prefs.js` alla radice di `src/`, e non contiene codice relativo al marketplace (è uno stub che delega alla GUI Python, vedi [`docs/architecture.md`](architecture.md) sezione 4). L'eventuale UI marketplace, se implementata, andrebbe in `src/gui/components/settings/mcp.py` (Python/GTK4), non in GJS.

## Test Verificati

### Backend
```python
# test_installer.py
await installer.install_server('test-server', {...}, {})
await installer.test_server('test-server')
await installer.update_server_env('test-server', {'API_KEY': 'test'})
await installer.uninstall_server('test-server')

# test_registry.py
await registry.get_featured()  # 4 server featured
await registry.search('database')  # filtra
await registry.search('web')  # fallback a featured
await registry.get_categories()  # ['Desktop', 'Web', 'Productivity', 'Data']
```

### Frontend (manual)
- ✅ Load marketplace featured in grid
- ✅ Search real-time filtra risultati
- ✅ Install dialog con env var form
- ✅ Installed tab mostra server con toggle
- ✅ Config button clickabile (placeholder)
- ✅ Uninstall rimuove da list

## Roadmap Futura

- [ ] **Health Status**: WebSocket polling per verificare "server ok" / "server down"
- [ ] **Version Management**: Traccia versione installata, proponi upgrade
- [ ] **Auto-start Toggle**: Per server che non devono attivarsi subito
- [ ] **Advanced Config UI**: Dialog per edit env vars post-installation
- [ ] **Categories Grid**: Browsing per categoria (Desktop, Web, Data, etc)
- [ ] **Reviews/Ratings**: Integrazione feedback da registry
- [ ] **Dependency Resolution**: Installa automaticamente `uvx`, `npx`, etc
- [ ] **CLI Tool**: `voice-assistant-mcp install sqlite-db` da terminal

## Note di Implementazione

1. **Isolamento D-Bus**: MCPManager espone metodi che ritornano JSON stringhe (non object), safer per D-Bus
2. **Fallback Offline**: Se api.smithery.ai non raggiungibile, usa FEATURED_SERVERS hardcoded
3. **Built-in Protection**: Server "gnome-system" non può essere disinstallato
4. **Timeouts**: 5s per fetch remote, 10s per test server startup
5. **Error Handling**: Tutti i metodi ritornano (success: bool, message: str) strutturato
6. **Env Var Security**: **già implementato**, non un TODO — `src/daemon/mcp/credentials.py` (`MCPCredentialStore`) salva i segreti nel keyring di sistema (`keyring.set_password`) e scrive in `mcp_servers.json` solo un riferimento `{"keyring": credential_name}`, non il valore in chiaro. `installer.py` la usa già nel flusso di installazione.
