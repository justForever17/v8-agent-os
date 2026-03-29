import { NextResponse } from "next/server";

function fallbackPayload(fallbackError: string) {
    return { error: fallbackError };
}

async function readJsonOrTextPayload(response: Response, fallbackError: string) {
    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.includes("application/json")) {
        const json = await response.json().catch(() => null);
        if (json && typeof json === "object") {
            return json;
        }
    }

    const text = await response.text().catch(() => "");
    return text.trim() ? { error: text.trim() } : fallbackPayload(fallbackError);
}

export function jsonProxyError(error: string, status: number) {
    return NextResponse.json({ error }, { status });
}

export async function relayJsonProxyResponse(response: Response, fallbackError: string) {
    if (!response.ok) {
        const payload = await readJsonOrTextPayload(response, fallbackError);
        return NextResponse.json(payload, { status: response.status });
    }

    const payload = await response.json().catch(() => ({}));
    return NextResponse.json(payload, { status: response.status });
}

type RelayStreamOptions = {
    cacheControl?: string;
    defaultContentType: string;
    preserveHeaders?: string[];
    successStatus?: number;
    applyHeaders?: (headers: Headers) => void;
};

export function relayStreamProxyResponse(response: Response, options: RelayStreamOptions) {
    const headers = new Headers();
    headers.set("Content-Type", response.headers.get("Content-Type") || options.defaultContentType);

    if (options.cacheControl) {
        headers.set("Cache-Control", options.cacheControl);
    }

    for (const headerName of options.preserveHeaders || []) {
        const value = response.headers.get(headerName);
        if (value) {
            headers.set(headerName, value);
        }
    }

    options.applyHeaders?.(headers);

    return new NextResponse(response.body, {
        status: options.successStatus ?? response.status,
        headers,
    });
}
