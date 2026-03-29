
"use client";

import { Layout, Maximize2, Download, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogTrigger, DialogClose } from "@/components/ui/dialog";

interface HTMLFileCardProps {
    url: string;
    filename?: string;
    filesize?: string; // Optional display string
}

export function HTMLFileCard({ url, filename, filesize }: HTMLFileCardProps) {
    const [isOpen, setIsOpen] = useState(false);

    // Ensure absolute URL
    const safeUrl = url.startsWith("http") ? url : `${window.location.origin}${url}`;

    // Extract filename from URL if not provided
    const displayFilename = filename || decodeURIComponent(url.split('/').pop()?.split('?')[0] || "document.html");

    const displaySize = filesize || "HTML";

    return (
        <div className="group relative w-full max-w-sm mt-2">
            {/* Card Container */}
            <div className="flex items-center gap-3 p-3 bg-card border rounded-xl shadow-sm hover:shadow-md transition-all duration-200 group-hover:border-primary/50">
                {/* Icon Thumbnail */}
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-blue-100 text-blue-600">
                    <Layout className="h-6 w-6" />
                </div>

                {/* Metadata */}
                <div className="flex-1 min-w-0 grid gap-0.5">
                    <div className="text-sm font-medium truncate" title={displayFilename}>
                        {displayFilename}
                    </div>
                    <div className="text-xs text-muted-foreground">
                        {displaySize}
                    </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1">
                    <Dialog open={isOpen} onOpenChange={setIsOpen}>
                        <DialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-primary" title="Preview">
                                <Maximize2 className="h-4 w-4" />
                            </Button>
                        </DialogTrigger>

                        {/* Fullscreen Preview Modal */}
                        <DialogContent className="max-w-[90vw] h-[90vh] p-0 flex flex-col gap-0 bg-background/95 backdrop-blur-sm border-none shadow-2xl [&>button]:hidden">
                            <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/40 shrink-0">
                                <div className="flex items-center gap-2">
                                    <div className="flex h-8 w-8 items-center justify-center rounded bg-blue-100 text-blue-600">
                                        <Layout className="h-4 w-4" />
                                    </div>
                                    <DialogTitle className="font-medium truncate max-w-[300px] text-base">{displayFilename}</DialogTitle>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Button variant="outline" size="sm" asChild className="gap-2 hidden sm:flex">
                                        <a href={safeUrl} download={displayFilename} target="_blank" rel="noopener noreferrer">
                                            <Download className="h-4 w-4" />
                                            Download
                                        </a>
                                    </Button>
                                    <DialogClose asChild>
                                        <Button variant="ghost" size="icon">
                                            <X className="h-5 w-5" />
                                        </Button>
                                    </DialogClose>
                                </div>
                            </div>

                            {/* Iframe Container */}
                            <div className="flex-1 w-full h-full bg-neutral-100 relative overflow-hidden">
                                <iframe
                                    src={safeUrl}
                                    className="w-full h-full border-none"
                                    title="HTML Viewer"
                                    loading="lazy"
                                    sandbox="allow-scripts allow-same-origin allow-popups"
                                />
                            </div>
                        </DialogContent>
                    </Dialog>

                    <Button variant="ghost" size="icon" asChild className="h-8 w-8 text-muted-foreground hover:text-primary" title="Open in New Tab">
                        <a href={safeUrl} target="_blank" rel="noopener noreferrer">
                            <Download className="h-4 w-4" />
                        </a>
                    </Button>
                </div>
            </div>
        </div>
    );
}
