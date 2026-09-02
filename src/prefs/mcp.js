import Gio from 'gi://Gio';

export function setupMcpPage(builder, settings, _) {
    const mcpPage = builder.get_object('mcp_page');
    // Reserved for MCP tools & server toggles UI expansion
    return { mcpPage };
}
