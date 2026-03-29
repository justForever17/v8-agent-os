"use client";

import { useState, useRef, DragEvent, ChangeEvent, useEffect, useCallback } from "react";
import { UploadCloud, FileText, Loader2, CheckCircle2, AlertCircle, X, Trash2, File as FileIcon, FileArchive, FileImage, FileCode, FileSpreadsheet } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export default function DocumentUploader() {
    const t = useT();
    const { locale } = useLocale();
    const [dragging, setDragging] = useState(false);
    const [files, setFiles] = useState<File[]>([]);
    const [uploading, setUploading] = useState(false);
    const [chunkSize, setChunkSize] = useState([1500]);
    const [chunkOverlap, setChunkOverlap] = useState([200]);
    const [result, setResult] = useState<{ status: 'success' | 'error', message: string } | null>(null);
    
    // Uploaded Documents state
    interface UploadedDoc {
        filename: string;
        chunk_count: number;
        uploaded_at: string;
    }
    const [documents, setDocuments] = useState<UploadedDoc[]>([]);
    const [loadingDocs, setLoadingDocs] = useState(false);
    const [deletingFile, setDeletingFile] = useState<string | null>(null);

    const formatDocumentDate = useCallback((value: string) => {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return value;
        }
        return date.toLocaleString(locale === "en" ? "en-US" : "zh-CN", {
            hour12: false,
        });
    }, [locale]);

    const loadDocuments = useCallback(async () => {
        setLoadingDocs(true);
        try {
            const res = await fetch("/api/memory/documents");
            const data = await res.json();
            if (res.ok) {
                setDocuments(data.documents || []);
            }
        } catch (err) {
            console.error("Failed to load documents", err);
        } finally {
            setLoadingDocs(false);
        }
    }, []);

    useEffect(() => {
        loadDocuments();
    }, [loadDocuments]);
    
    const fileInputRef = useRef<HTMLInputElement>(null);

    const onDragOver = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setDragging(true);
    };

    const onDragLeave = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setDragging(false);
    };

    const onDrop = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFiles(Array.from(e.dataTransfer.files));
        }
    };

    const onFileChange = (e: ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            handleFiles(Array.from(e.target.files));
        }
    };

    const handleFiles = (newFiles: File[]) => {
        setResult(null);
        setFiles(prev => [...prev, ...newFiles]);
    };

    const removeFile = (index: number) => {
        setFiles(files.filter((_, i) => i !== index));
    };

    const handleUpload = async () => {
        if (files.length === 0) return;
        
        setUploading(true);
        setResult(null);
        
        const formData = new FormData();
        files.forEach(file => formData.append("files", file));
        formData.append("chunk_size", chunkSize[0].toString());
        formData.append("chunk_overlap", chunkOverlap[0].toString());
        
        try {
            const res = await fetch("/api/memory/upload", {
                method: "POST",
                body: formData,
            });
            const data = await res.json();
            
            if (!res.ok) {
                throw new Error(data.error || "Upload failed");
            }
            
            setResult({ status: 'success', message: data.message });
            setFiles([]); // clear list on success
            loadDocuments(); // Refresh uploaded list
        } catch (err: any) {
            setResult({ status: 'error', message: String(err.message || err) });
        } finally {
            setUploading(false);
        }
    };

    const handleDeleteDocument = async (filename: string, chunkCount: number) => {
        if (!confirm(t(lt(`确定要卸载文档 "${filename}" 及其 ${chunkCount} 个语义切块吗？(将从知识库和向量库中彻底删除)`, `Remove "${filename}" and its ${chunkCount} semantic chunks from knowledge and vectors?`)))) return;
        
        setDeletingFile(filename);
        try {
            const res = await fetch(`/api/memory/documents/${encodeURIComponent(filename)}`, {
                method: "DELETE"
            });
            if (res.ok) {
                loadDocuments();
            } else {
                const data = await res.json();
                alert(`${t(lt("卸载失败", "Remove failed"))}: ${data.error}`);
            }
        } catch (err) {
            console.error("Failed to delete", err);
            alert(t(lt("卸载出错", "Remove failed")));
        } finally {
            setDeletingFile(null);
        }
    };

    const getFileIcon = (filename: string) => {
        const ext = filename.split('.').pop()?.toLowerCase();
        if (['pdf'].includes(ext || '')) return <FileText className="w-6 h-6 text-red-500" />;
        if (['doc', 'docx'].includes(ext || '')) return <FileText className="w-6 h-6 text-blue-500" />;
        if (['xls', 'xlsx', 'csv'].includes(ext || '')) return <FileSpreadsheet className="w-6 h-6 text-green-500" />;
        if (['zip', 'tar', 'gz'].includes(ext || '')) return <FileArchive className="w-6 h-6 text-yellow-600" />;
        if (['json', 'xml', 'md', 'mdx', 'html'].includes(ext || '')) return <FileCode className="w-6 h-6 text-slate-500" />;
        return <FileIcon className="w-6 h-6 text-muted-foreground" />;
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-xl flex items-center gap-2">
                    <UploadCloud className="w-5 h-5" />
                    {t("知识库文档上传")}
                </CardTitle>
                <CardDescription>
                    {t("支持 PDF, Docx, TXT, Markdown, CSV, Excel, PPTX, HTML 等格式。上传后将自动进行文本解析与基于 Markdown 标题的语义切分，并完成向量化与 FTS 索引，供 Agent 进行 RAG 检索。")}
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                
                {/* Drag and drop zone */}
                <div 
                    className={`border-2 border-dashed rounded-xl p-8 transition-colors flex flex-col items-center justify-center gap-2 text-center relative
                        ${dragging ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/30'}
                    `}
                    onDragOver={onDragOver}
                    onDragLeave={onDragLeave}
                    onDrop={onDrop}
                >
                    <div className="p-4 bg-muted rounded-full">
                        <UploadCloud className="w-8 h-8 text-muted-foreground" />
                    </div>
                    <div className="mt-2 text-sm font-medium">{t("将文件拖拽于此，或")}</div>
                    <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                        {t("浏览文件")}
                    </Button>
                    <input 
                        type="file" 
                        multiple 
                        className="hidden" 
                        ref={fileInputRef} 
                        onChange={onFileChange} 
                        disabled={uploading}
                    />
                    <p className="text-xs text-muted-foreground mt-2 max-w-sm">
                        {t("推荐单个文件大小不要超过 20MB。系统自动过滤不受支持的扩展名。")}
                    </p>
                </div>

                {/* File list */}
                {files.length > 0 && (
                    <div className="space-y-2">
                        <Label>{t("待上传文件")} ({files.length})</Label>
                        <div className="max-h-48 overflow-y-auto space-y-2 pr-2">
                            {files.map((file, i) => (
                                <div key={i} className="flex items-center justify-between p-2 rounded-md border bg-muted/20 text-sm group">
                                    <div className="flex items-center gap-2 overflow-hidden">
                                        <FileText className="w-4 h-4 shrink-0 text-muted-foreground" />
                                        <span className="truncate" title={file.name}>{file.name}</span>
                                        <span className="text-xs text-muted-foreground ml-2 shrink-0">
                                            {(file.size / 1024 / 1024).toFixed(2)} MB
                                        </span>
                                    </div>
                                    <Button 
                                        variant="ghost" 
                                        size="icon" 
                                        className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity rounded-full hover:bg-destructive/10 hover:text-destructive"
                                        onClick={() => removeFile(i)}
                                        disabled={uploading}
                                    >
                                        <X className="w-3 h-3" />
                                    </Button>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Settings */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t">
                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <Label>{t("语义块大小 (Chunk Size)")}</Label>
                            <span className="text-xs text-muted-foreground tabular-nums bg-muted px-2 py-0.5 rounded-md">{chunkSize[0]}</span>
                        </div>
                        <Slider 
                            value={chunkSize} 
                            onValueChange={setChunkSize} 
                            min={200} 
                            max={4000} 
                            step={100} 
                            disabled={uploading}
                        />
                        <p className="text-[10px] text-muted-foreground">{t("影响最大检索分块长度。文档将优先按标题进行语义切分。")}</p>
                    </div>

                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <Label>{t("分块重叠度 (Chunk Overlap)")}</Label>
                            <span className="text-xs text-muted-foreground tabular-nums bg-muted px-2 py-0.5 rounded-md">{chunkOverlap[0]}</span>
                        </div>
                        <Slider 
                            value={chunkOverlap} 
                            onValueChange={setChunkOverlap} 
                            min={0} 
                            max={1000} 
                            step={50} 
                            disabled={uploading}
                        />
                        <p className="text-[10px] text-muted-foreground">{t("当退化为固定字数切块时，相邻块之间的字符重叠量。")}</p>
                    </div>
                </div>

                {/* Result Message */}
                {result && (
                    <div className={`p-4 rounded-lg flex items-start gap-3 border ${
                        result.status === 'success' 
                            ? 'bg-green-500/10 border-green-500/20 text-green-600 dark:text-green-400' 
                            : 'bg-destructive/10 border-destructive/20 text-destructive'
                    }`}>
                        {result.status === 'success' ? <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" /> : <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />}
                        <div className="text-sm font-medium">{result.message}</div>
                    </div>
                )}
            </CardContent>
            <CardFooter className="flex justify-between border-t bg-muted/10 p-4">
                <p className="text-xs text-muted-foreground">
                    {t("所有文件处理完毕后，您可以直接在「知识库」或「Agent 助手」中测试检索效果。")}
                </p>
                <Button onClick={handleUpload} disabled={files.length === 0 || uploading} className="min-w-[120px]">
                    {uploading ? (
                        <>
                            <Loader2 className="w-4 h-4 mr-2 animate-spin" /> {t("正在处理...")}
                        </>
                    ) : (
                        <>
                            <UploadCloud className="w-4 h-4 mr-2" /> {t("开始上传并解析")}
                        </>
                    )}
                </Button>
            </CardFooter>

            {/* List of uploaded documents */}
            {documents.length > 0 && (
                <div className="border-t bg-muted/5 p-6">
                    <h3 className="font-semibold text-sm mb-4 flex items-center gap-2">
                        {t("已存在于 RAG 中的文档")} ({documents.length})
                        {loadingDocs && <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />}
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                        {documents.map((doc, idx) => (
                            <div key={idx} className="flex flex-col p-4 border rounded-xl bg-card hover:bg-accent/10 transition-colors shadow-sm relative group overflow-hidden">
                                <div className="flex items-start gap-3">
                                    <div className="shrink-0 p-2 bg-muted rounded-lg">
                                        {getFileIcon(doc.filename)}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-semibold truncate" title={doc.filename}>{doc.filename}</p>
                                        <div className="flex items-center gap-2 mt-1">
                                            <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-mono">{doc.chunk_count} {t(lt("块", "chunks"))}</span>
                                            <span className="text-xs text-muted-foreground truncate" title={formatDocumentDate(doc.uploaded_at)}>
                                                {formatDocumentDate(doc.uploaded_at)}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                                    <Button 
                                        variant="destructive" 
                                        size="icon" 
                                        className="h-8 w-8 rounded-full shadow-sm"
                                        onClick={() => handleDeleteDocument(doc.filename, doc.chunk_count)}
                                        disabled={deletingFile === doc.filename}
                                        title={t("卸载该文档")}
                                    >
                                        {deletingFile === doc.filename 
                                            ? <Loader2 className="w-4 h-4 animate-spin" />
                                            : <Trash2 className="w-4 h-4" />
                                        }
                                    </Button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </Card>
    );
}
