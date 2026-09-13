"""Keep SDK diagnostics from serializing private endpoints or wire payloads."""
import contextvars
import logging

private_transport = contextvars.ContextVar("mcp_private_transport", default=False)


class PrivateTransportFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not private_transport.get():
            return True
        if record.levelno < logging.WARNING:
            return False
        # SDK f-strings and exc_info may both include the dedicated URL, bearer
        # headers or request bodies. Our connection state holds the safe reason.
        record.msg = "MCP transport reported a connection problem; see the server connection status."
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


for logger_name in ("httpx", "mcp.client.streamable_http", "mcp.client.sse", "mcp.shared.session"):
    logging.getLogger(logger_name).addFilter(PrivateTransportFilter())
