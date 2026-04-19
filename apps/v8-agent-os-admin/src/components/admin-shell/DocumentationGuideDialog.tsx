"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

type DocumentationGuideDialogProps = {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    description?: string;
    content: string;
};

export function DocumentationGuideDialog({
    open,
    onOpenChange,
    title,
    description,
    content,
}: DocumentationGuideDialogProps) {
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-[700px]">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    {description ? <DialogDescription>{description}</DialogDescription> : null}
                </DialogHeader>
                <div className="prose prose-sm max-w-none dark:prose-invert">
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            h1: ({ ...props }) => (
                                <h1 className="mb-4 mt-6 text-2xl font-bold" {...props} />
                            ),
                            h2: ({ ...props }) => (
                                <h2 className="mb-3 mt-5 border-b pb-2 text-xl font-semibold" {...props} />
                            ),
                            h3: ({ ...props }) => (
                                <h3 className="mb-2 mt-4 text-lg font-medium" {...props} />
                            ),
                            p: ({ ...props }) => (
                                <p className="mb-4 leading-relaxed text-muted-foreground" {...props} />
                            ),
                            ul: ({ ...props }) => (
                                <ul className="mb-4 ml-6 list-disc space-y-1" {...props} />
                            ),
                            ol: ({ ...props }) => (
                                <ol className="mb-4 ml-6 list-decimal space-y-1" {...props} />
                            ),
                            li: ({ ...props }) => (
                                <li className="text-muted-foreground" {...props} />
                            ),
                            strong: ({ ...props }) => (
                                <strong className="font-semibold text-foreground" {...props} />
                            ),
                            blockquote: ({ ...props }) => (
                                <blockquote
                                    className="my-4 rounded-r-md border-l-4 border-primary bg-muted p-2 pl-4 italic text-muted-foreground"
                                    {...props}
                                />
                            ),
                            code: ({ className, children, ...props }) => {
                                const match = /language-(\w+)/.exec(className || "");
                                return match ? (
                                    <pre className="my-4 block overflow-x-auto rounded-md bg-muted p-4">
                                        <code className={className} {...props}>
                                            {children}
                                        </code>
                                    </pre>
                                ) : (
                                    <code
                                        className="rounded bg-muted px-[0.3rem] py-[0.2rem] font-mono text-sm text-primary"
                                        {...props}
                                    >
                                        {children}
                                    </code>
                                );
                            },
                        }}
                    >
                        {content}
                    </ReactMarkdown>
                </div>
            </DialogContent>
        </Dialog>
    );
}
