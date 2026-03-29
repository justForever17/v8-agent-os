@asynccontextmanager
async def stdio_client(server: StdioServerParameters, errlog: TextIO = sys.stderr):
    """
    Client transport for stdio: this will connect to a server by spawning a
    process and communicating with it over stdin/stdout.
    """
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    try:
        command = _get_executable_command(server.command)

        # Open process with stderr piped for capture
        process = await _create_platform_compatible_process(
            command=command,
            args=server.args,
            env=({**get_default_environment(), **server.env} if server.env is not None else get_default_environment()),
            errlog=errlog,
            cwd=server.cwd,
        )
    except OSError:
        # Clean up streams if process creation fails
        await read_stream.aclose()
        await write_stream.aclose()
        await read_stream_writer.aclose()
        await write_stream_reader.aclose()
        raise

    async def stdout_reader():
        assert process.stdout, "Opened process is missing stdout"

        try:
            async with read_stream_writer:
                buffer = ""
                async for chunk in TextReceiveStream(
                    process.stdout,
                    encoding=server.encoding,
                    errors=server.encoding_error_handler,
                ):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:  # pragma: no cover
                            logger.exception("Failed to parse JSONRPC message from server")
                            await read_stream_writer.send(exc)
                            continue

                        session_message = SessionMessage(message)
                        await read_stream_writer.send(session_message)
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdin_writer():
        assert process.stdin, "Opened process is missing stdin"

        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    json = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await process.stdin.send(
                        (json + "\n").encode(
                            encoding=server.encoding,
                            errors=server.encoding_error_handler,
                        )
                    )
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with (
        anyio.create_task_group() as tg,
        process,
    ):
        tg.start_soon(stdout_reader)
        tg.start_soon(stdin_writer)
        try:
            yield read_stream, write_stream
        finally:
            # MCP spec: stdio shutdown sequence
            # 1. Close input stream to server
            # 2. Wait for server to exit, or send SIGTERM if it doesn't exit in time
            # 3. Send SIGKILL if still not exited
            if process.stdin:  # pragma: no branch
                try:
                    await process.stdin.aclose()
                except Exception:  # pragma: no cover
                    # stdin might already be closed, which is fine
                    pass

            try:
                # Give the process time to exit gracefully after stdin closes
                with anyio.fail_after(PROCESS_TERMINATION_TIMEOUT):
                    await process.wait()
            except TimeoutError:
                # Process didn't exit from stdin closure, use platform-specific termination
                # which handles SIGTERM -> SIGKILL escalation
                await _terminate_process_tree(process)
            except ProcessLookupError:  # pragma: no cover
                # Process already exited, which is fine
                pass
            await read_stream.aclose()
            await write_stream.aclose()
            await read_stream_writer.aclose()
            await write_stream_reader.aclose()

