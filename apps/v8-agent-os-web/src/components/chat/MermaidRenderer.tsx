"use client";

import mermaid from 'mermaid';
import { useEffect, useState, useId } from 'react';
import { ZoomIn, ZoomOut, Copy, Check, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useDebouncedValue } from '@/hooks/use-debounce';

export function MermaidRenderer({ code, className }: { code: string; className?: string }) {
    const [svg, setSvg] = useState<string>('');
    const [error, setError] = useState<string | null>(null);
    const [scale, setScale] = useState(1);
    const [isCopied, setIsCopied] = useState(false);
    const uniqueId = useId().replace(/:/g, ''); // React ID 包含冒号，mermaid 不喜欢

    // 防抖代码更新，避免流式输入时过于频繁的渲染
    const debouncedCode = useDebouncedValue(code, 300);

    useEffect(() => {
        // 初始化 mermaid (根据是否是暗色模式可以动态调整，这里暂时默认暗色)
        mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            securityLevel: 'loose',
            fontFamily: 'inherit'
        });
    }, []);

    useEffect(() => {
        if (!debouncedCode) return;

        const renderChart = async () => {
            try {
                setError(null);
                // 生成唯一 ID
                const id = `mermaid-${uniqueId}-${Date.now()}`;
                const { svg } = await mermaid.render(id, debouncedCode);
                setSvg(svg);
            } catch (err) {
                console.error("Mermaid Render Error:", err);
                // 尝试提取更友好的错误信息
                setError(err instanceof Error ? err.message : String(err));
            }
        };

        renderChart();
    }, [debouncedCode, uniqueId]);

    const copyToClipboard = async () => {
        await navigator.clipboard.writeText(code);
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), 2000);
    };

    const downloadSvg = () => {
        const blob = new Blob([svg], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'chart.svg';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    if (error) {
        return (
            <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-4 text-sm text-red-500 my-4">
                <div className="font-semibold mb-2">Mermaid Render Error</div>
                <pre className="whitespace-pre-wrap font-mono text-xs opacity-80">{error}</pre>
                <div className="mt-4 pt-4 border-t border-red-500/20">
                    <div className="text-xs text-muted-foreground mb-2">Raw Code:</div>
                    <pre className="font-mono text-xs bg-black/20 p-2 rounded overflow-x-auto">
                        {code}
                    </pre>
                </div>
            </div>
        );
    }

    return (
        <div className={cn("my-4 rounded-lg border bg-card text-card-foreground shadow-sm overflow-hidden", className)}>
            <div className="flex items-center justify-between px-3 py-2 border-b bg-muted/30">
                <div className="text-xs font-medium text-muted-foreground">Mermaid Chart</div>
                <div className="flex items-center gap-1">
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setScale(s => Math.max(0.5, s - 0.1))}>
                        <ZoomOut className="h-3.5 w-3.5" />
                    </Button>
                    <span className="text-xs w-8 text-center text-muted-foreground">{Math.round(scale * 100)}%</span>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setScale(s => Math.min(2, s + 0.1))}>
                        <ZoomIn className="h-3.5 w-3.5" />
                    </Button>
                    <div className="w-px h-3 bg-border mx-1" />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={copyToClipboard}>
                        {isCopied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={downloadSvg}>
                        <Download className="h-3.5 w-3.5" />
                    </Button>
                </div>
            </div>
            <div className="p-4 overflow-auto flex justify-center bg-[#0d1117]"> {/* Github Dark bg for better contrast */}
                <div
                    dangerouslySetInnerHTML={{ __html: svg }}
                    style={{ transform: `scale(${scale})`, transformOrigin: 'center top', transition: 'transform 0.2s' }}
                    className="mermaid-svg-container"
                />
            </div>
        </div>
    );
}
