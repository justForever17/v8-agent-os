"use client";

import * as React from "react";
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Check, Copy, FileCode, Eye, Download, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { MermaidRenderer } from "./MermaidRenderer";


interface CodeBlockProps {
    language: string;
    value: string;
    className?: string;
    isStreaming?: boolean;
}

export function CodeBlock({ language, value, className, isStreaming }: CodeBlockProps) {
    const [isCopied, setIsCopied] = React.useState(false);
    const [isPreviewOpen, setIsPreviewOpen] = React.useState(false);

    const copyToClipboard = async () => {
        if (!value) return;
        await navigator.clipboard.writeText(value);
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), 2000);
    };

    const downloadFile = () => {
        const blob = new Blob([value], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'index.html';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const normalizedLang = language?.toLowerCase();

    // 1. Mermaid support
    if (normalizedLang === 'mermaid') {
        return <MermaidRenderer code={value} className={className} />;
    }

    // 2. HTML Preview support - DISABLED
    // We want Agents to always show source code. Previews are handled by Supervisor via <artifact> tags.
    /*
    const isHtml = normalizedLang === 'html' || normalizedLang === 'xml';
    if (isHtml && !isStreaming) {
       // ... preview logic removed ...
    }
    */

    // 3. Standard Code Block
    return (
        <div className="relative rounded-md overflow-hidden my-2 border">
            <div className="flex items-center justify-between bg-muted px-4 py-1.5 text-xs text-muted-foreground border-b">
                <span>{language}</span>
                <Button
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6 -mr-2 text-muted-foreground hover:text-foreground"
                    onClick={copyToClipboard}
                >
                    {isCopied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                </Button>
            </div>
            <div className="overflow-x-auto w-full max-h-[500px] overflow-y-auto custom-scrollbar">
                <SyntaxHighlighter
                    style={vscDarkPlus}
                    language={language}
                    PreTag="div"
                    customStyle={{ margin: 0, width: '100%' }}
                    wrapLines={true}
                    wrapLongLines={true}
                    {...{ className }}
                >
                    {value}
                </SyntaxHighlighter>
            </div>
        </div>
    );
}
