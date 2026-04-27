"use client";
import { useState, useRef, DragEvent, ChangeEvent, useEffect, useCallback } from "react";
import { UploadCloud, FileText, Loader2, CheckCircle2, AlertCircle, X, Trash2, File as FileIcon, FileArchive, FileCode, FileSpreadsheet } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
export default function DocumentUploader() {
    const t = useT();
    const { locale } = useLocale();
    const [dragging, setDragging] = useState(false);
    const [files, setFiles] = useState<File[]>([]);
    const [uploading, setUploading] = useState(false);
    const [chunkSize, setChunkSize] = useState([1500]);
    const [chunkOverlap, setChunkOverlap] = useState([200]);
    const [trustedUpload, setTrustedUpload] = useState(false);
    const [result, setResult] = useState<{
        status: 'success' | 'error';
        message: string;
        details?: string[];
    } | null>(null);
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
        }
        catch (err) {
            console.error("Failed to load documents", err);
        }
        finally {
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
        if (files.length === 0)
            return;
        setUploading(true);
        setResult(null);
        const formData = new FormData();
        files.forEach(file => formData.append("files", file));
        formData.append("chunk_size", chunkSize[0].toString());
        formData.append("chunk_overlap", chunkOverlap[0].toString());
        formData.append("trusted_upload", trustedUpload ? "true" : "false");
        try {
            const res = await fetch("/api/memory/upload", {
                method: "POST",
                body: formData,
            });
            const data = await res.json();
            if (!res.ok) {
                const detail = data?.detail || {};
                const missingDependencies = Array.isArray(detail?.details?.missingDependencies)
                    ? detail.details.missingDependencies.map((item: unknown) => String(item))
                    : [];
                setResult({
                    status: 'error',
                    message: String(detail?.message || data?.error || "Upload failed"),
                    details: missingDependencies.length > 0
                        ? [
                            `${t("components.memory.DocumentUploader.keb59465e")}: ${missingDependencies.join(", ")}`,
                            `${t("components.memory.DocumentUploader.kb4677265")}: ${String(detail?.details?.requiredBundle || "document-ingestion")}`,
                        ]
                        : undefined,
                });
                return;
            }
            setResult({ status: 'success', message: data.message });
            setFiles([]); // clear list on success
            loadDocuments(); // Refresh uploaded list
        }
        catch (err: unknown) {
            const errorMessage = err instanceof Error ? err.message : String(err);
            setResult({ status: 'error', message: errorMessage });
        }
        finally {
            setUploading(false);
        }
    };
    const handleDeleteDocument = async (filename: string, chunkCount: number) => {
        if (!confirm(t("components.memory.DocumentUploader.k5fef2691", {
            filename: filename,
            chunkCount: chunkCount
        })))
            return;
        setDeletingFile(filename);
        try {
            const res = await fetch(`/api/memory/documents/${encodeURIComponent(filename)}`, {
                method: "DELETE"
            });
            if (res.ok) {
                loadDocuments();
            }
            else {
                const data = await res.json();
                alert(`${t("components.memory.DocumentUploader.k0ef3cd3b")}: ${data.error}`);
            }
        }
        catch (err) {
            console.error("Failed to delete", err);
            alert(t("components.memory.DocumentUploader.ka46db6bc"));
        }
        finally {
            setDeletingFile(null);
        }
    };
    const getFileIcon = (filename: string) => {
        const ext = filename.split('.').pop()?.toLowerCase();
        if (['pdf'].includes(ext || ''))
            return <FileText className="w-6 h-6 text-red-500"/>;
        if (['doc', 'docx'].includes(ext || ''))
            return <FileText className="w-6 h-6 text-blue-500"/>;
        if (['xls', 'xlsx', 'csv'].includes(ext || ''))
            return <FileSpreadsheet className="w-6 h-6 text-green-500"/>;
        if (['zip', 'tar', 'gz'].includes(ext || ''))
            return <FileArchive className="w-6 h-6 text-yellow-600"/>;
        if (['json', 'xml', 'md', 'mdx', 'html'].includes(ext || ''))
            return <FileCode className="w-6 h-6 text-slate-500"/>;
        return <FileIcon className="w-6 h-6 text-muted-foreground"/>;
    };
    return (<Card>
            <CardHeader>
                <CardTitle className="text-xl flex items-center gap-2">
                    <UploadCloud className="w-5 h-5"/>
                    {t("components.memory.DocumentUploader.k29e2e916")}
                </CardTitle>
                <CardDescription>
                    {t("components.memory.DocumentUploader.k6ed36052")}
                </CardDescription>
                <p className="text-xs leading-5 text-muted-foreground">
                    {t("components.memory.DocumentUploader.kc0e099b8")}
                </p>
            </CardHeader>
            <CardContent className="space-y-6">
                
                {/* Drag and drop zone */}
                <div className={`border-2 border-dashed rounded-xl p-8 transition-colors flex flex-col items-center justify-center gap-2 text-center relative
                        ${dragging ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/30'}
                    `} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
                    <div className="p-4 bg-muted rounded-full">
                        <UploadCloud className="w-8 h-8 text-muted-foreground"/>
                    </div>
                    <div className="mt-2 text-sm font-medium">{t("components.memory.DocumentUploader.k43a124c9")}</div>
                    <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                        {t("components.memory.DocumentUploader.k195541de")}
                    </Button>
                    <input type="file" multiple className="hidden" ref={fileInputRef} onChange={onFileChange} disabled={uploading}/>
                    <p className="text-xs text-muted-foreground mt-2 max-w-sm">
                        {t("components.memory.DocumentUploader.k4b4bb6ab")}
                    </p>
                </div>

                {/* File list */}
                {files.length > 0 && (<div className="space-y-2">
                        <Label>{t("components.memory.DocumentUploader.k9a0f5c38")} ({files.length})</Label>
                        <div className="max-h-48 overflow-y-auto space-y-2 pr-2">
                            {files.map((file, i) => (<div key={i} className="flex items-center justify-between p-2 rounded-md border bg-muted/20 text-sm group">
                                    <div className="flex items-center gap-2 overflow-hidden">
                                        <FileText className="w-4 h-4 shrink-0 text-muted-foreground"/>
                                        <span className="truncate" title={file.name}>{file.name}</span>
                                        <span className="text-xs text-muted-foreground ml-2 shrink-0">
                                            {(file.size / 1024 / 1024).toFixed(2)} MB
                                        </span>
                                    </div>
                                    <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity rounded-full hover:bg-destructive/10 hover:text-destructive" onClick={() => removeFile(i)} disabled={uploading}>
                                        <X className="w-3 h-3"/>
                                    </Button>
                                </div>))}
                        </div>
                    </div>)}

                {/* Settings */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t">
                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <Label>{t("components.memory.DocumentUploader.k25ee50c8")}</Label>
                            <span className="text-xs text-muted-foreground tabular-nums bg-muted px-2 py-0.5 rounded-md">{chunkSize[0]}</span>
                        </div>
                        <Slider value={chunkSize} onValueChange={setChunkSize} min={200} max={4000} step={100} disabled={uploading}/>
                        <p className="text-[10px] text-muted-foreground">{t("components.memory.DocumentUploader.k13292e9b")}</p>
                    </div>

                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <Label>{t("components.memory.DocumentUploader.ka0625a49")}</Label>
                            <span className="text-xs text-muted-foreground tabular-nums bg-muted px-2 py-0.5 rounded-md">{chunkOverlap[0]}</span>
                        </div>
                        <Slider value={chunkOverlap} onValueChange={setChunkOverlap} min={0} max={1000} step={50} disabled={uploading}/>
                        <p className="text-[10px] text-muted-foreground">{t("components.memory.DocumentUploader.k7ba56eda")}</p>
                    </div>
                    <label className="flex gap-3 rounded-xl border border-dashed bg-muted/20 p-3 md:col-span-2">
                        <Checkbox checked={trustedUpload} onCheckedChange={(value) => setTrustedUpload(Boolean(value))} disabled={uploading}/>
                        <span className="space-y-1">
                            <span className="block text-sm font-medium">{t("components.memory.DocumentUploader.trustedUploadTitle")}</span>
                            <span className="block text-xs leading-5 text-muted-foreground">{t("components.memory.DocumentUploader.trustedUploadDescription")}</span>
                        </span>
                    </label>
                </div>

                {/* Result Message */}
                {result && (<div className={`p-4 rounded-lg flex items-start gap-3 border ${result.status === 'success'
                ? 'bg-green-500/10 border-green-500/20 text-green-600 dark:text-green-400'
                : 'bg-destructive/10 border-destructive/20 text-destructive'}`}>
                        {result.status === 'success' ? <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5"/> : <AlertCircle className="w-5 h-5 shrink-0 mt-0.5"/>}
                        <div className="space-y-1 text-sm">
                            <div className="font-medium">{result.message}</div>
                            {result.details?.length ? (<ul className="list-disc space-y-1 pl-4 text-xs opacity-90">
                                    {result.details.map((detail) => (<li key={detail}>{detail}</li>))}
                                </ul>) : null}
                        </div>
                    </div>)}
            </CardContent>
            <CardFooter className="flex justify-between border-t bg-muted/10 p-4">
                <p className="text-xs text-muted-foreground">
                    {t("components.memory.DocumentUploader.kbc3d4545")}
                </p>
                <Button onClick={handleUpload} disabled={files.length === 0 || uploading} className="min-w-[120px]">
                    {uploading ? (<>
                            <Loader2 className="w-4 h-4 mr-2 animate-spin"/> {t("components.memory.DocumentUploader.k38a6f762")}
                        </>) : (<>
                            <UploadCloud className="w-4 h-4 mr-2"/> {t("components.memory.DocumentUploader.k401aa205")}
                        </>)}
                </Button>
            </CardFooter>

            {/* List of uploaded documents */}
            {documents.length > 0 && (<div className="border-t bg-muted/5 p-6">
                    <h3 className="font-semibold text-sm mb-4 flex items-center gap-2">
                        {t("components.memory.DocumentUploader.kefd10a96")} ({documents.length})
                        {loadingDocs && <Loader2 className="w-3 h-3 animate-spin text-muted-foreground"/>}
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                        {documents.map((doc, idx) => (<div key={idx} className="flex flex-col p-4 border rounded-xl bg-card hover:bg-accent/10 transition-colors shadow-sm relative group overflow-hidden">
                                <div className="flex items-start gap-3">
                                    <div className="shrink-0 p-2 bg-muted rounded-lg">
                                        {getFileIcon(doc.filename)}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-semibold truncate" title={doc.filename}>{doc.filename}</p>
                                        <div className="flex items-center gap-2 mt-1">
                                            <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-mono">{doc.chunk_count} {t("components.memory.DocumentUploader.k90936257")}</span>
                                            <span className="text-xs text-muted-foreground truncate" title={formatDocumentDate(doc.uploaded_at)}>
                                                {formatDocumentDate(doc.uploaded_at)}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                                    <Button variant="destructive" size="icon" className="h-8 w-8 rounded-full shadow-sm" onClick={() => handleDeleteDocument(doc.filename, doc.chunk_count)} disabled={deletingFile === doc.filename} title={t("components.memory.DocumentUploader.k7e0a45a4")}>
                                        {deletingFile === doc.filename
                    ? <Loader2 className="w-4 h-4 animate-spin"/>
                    : <Trash2 className="w-4 h-4"/>}
                                    </Button>
                                </div>
                            </div>))}
                    </div>
                </div>)}
        </Card>);
}
