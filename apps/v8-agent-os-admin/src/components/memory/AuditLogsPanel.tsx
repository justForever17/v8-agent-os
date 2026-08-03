"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Loader2, RefreshCw, Activity } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { formatLocalDateTime } from "@/lib/time";
import { useT } from "@/components/providers/LocaleProvider";
interface AuditLog {
    id: string;
    timestamp: string;
    source_type: string;
    action: string;
    status: string;
    details?: string;
}
const POLL_INTERVAL_MS = 30000; // 30 seconds
export default function AuditLogsPanel() {
    const t = useT();
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(0);
    const [hasMore, setHasMore] = useState(false);
    const pageSize = 20;
    // Filters
    const [sourceType, setSourceType] = useState<string>("ALL");
    const [statusFilter, setStatusFilter] = useState<string>("ALL");
    // Auto-refresh
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [countdown, setCountdown] = useState(POLL_INTERVAL_MS / 1000);
    const [clearing, setClearing] = useState(false);
    const intervalRef = useRef<NodeJS.Timeout | null>(null);
    const countdownRef = useRef<NodeJS.Timeout | null>(null);
    // New-entry highlight: track IDs from previous fetch
    const prevLogIdsRef = useRef<Set<string>>(new Set());
    const [newLogIds, setNewLogIds] = useState<Set<string>>(new Set());
    const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);
    const fetchLogs = useCallback(async (isLoadMore = false) => {
        try {
            setLoading(true);
            const currentPage = isLoadMore ? page + 1 : 0;
            if (!isLoadMore)
                setPage(0);
            const params = new URLSearchParams({
                limit: pageSize.toString(),
                offset: (currentPage * pageSize).toString()
            });
            if (sourceType !== "ALL")
                params.append("source_type", sourceType);
            if (statusFilter !== "ALL")
                params.append("status", statusFilter);
            const res = await fetch(`/api/audit/logs?${params.toString()}`);
            if (res.ok) {
                const data = await res.json();
                if (isLoadMore) {
                    setLogs(prev => [...prev, ...data.logs]);
                }
                else {
                    // Compute newly appeared IDs
                    const incomingIds = new Set<string>((data.logs as AuditLog[]).map((l: AuditLog) => l.id));
                    if (prevLogIdsRef.current.size > 0) {
                        const freshIds = new Set<string>();
                        incomingIds.forEach(id => {
                            if (!prevLogIdsRef.current.has(id))
                                freshIds.add(id);
                        });
                        setNewLogIds(freshIds);
                    }
                    prevLogIdsRef.current = incomingIds;
                    setLogs(data.logs);
                }
                setHasMore(data.logs.length === pageSize);
                if (isLoadMore)
                    setPage(currentPage);
            }
        }
        catch (err) {
            console.error("Failed to load audit logs", err);
        }
        finally {
            setLoading(false);
        }
    }, [page, sourceType, statusFilter, pageSize]);
    // Initial fetch + filter change
    useEffect(() => {
        fetchLogs(false);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sourceType, statusFilter]);
    // Auto-refresh polling
    useEffect(() => {
        const clearTimers = () => {
            if (intervalRef.current) {
                clearInterval(intervalRef.current);
                intervalRef.current = null;
            }
            if (countdownRef.current) {
                clearInterval(countdownRef.current);
                countdownRef.current = null;
            }
        };
        if (autoRefresh) {
            setCountdown(POLL_INTERVAL_MS / 1000);
            intervalRef.current = setInterval(() => {
                fetchLogs(false);
                setCountdown(POLL_INTERVAL_MS / 1000);
            }, POLL_INTERVAL_MS);
            countdownRef.current = setInterval(() => {
                setCountdown(prev => (prev > 0 ? prev - 1 : POLL_INTERVAL_MS / 1000));
            }, 1000);
        }
        else {
            clearTimers();
        }
        // Pause when tab hidden
        const handleVisibility = () => {
            if (document.hidden) {
                clearTimers();
            }
            else if (autoRefresh) {
                fetchLogs(false);
                setCountdown(POLL_INTERVAL_MS / 1000);
                intervalRef.current = setInterval(() => {
                    fetchLogs(false);
                    setCountdown(POLL_INTERVAL_MS / 1000);
                }, POLL_INTERVAL_MS);
                countdownRef.current = setInterval(() => {
                    setCountdown(prev => (prev > 0 ? prev - 1 : POLL_INTERVAL_MS / 1000));
                }, 1000);
            }
        };
        document.addEventListener("visibilitychange", handleVisibility);
        return () => {
            clearTimers();
            document.removeEventListener("visibilitychange", handleVisibility);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [autoRefresh]);
    const handleClearLogs = useCallback(async () => {
        const sourceLabel = sourceType === "ALL" ? t("components.memory.AuditLogsPanel.kd2c62cf3") : sourceType;
        const statusLabel = statusFilter === "ALL" ? t("components.memory.AuditLogsPanel.k084157f0") : statusFilter;
        if (!window.confirm(t("components.memory.AuditLogsPanel.k3f5f134c", {
            sourceLabel: sourceLabel,
            statusLabel: statusLabel
        }))) {
            return;
        }
        setClearing(true);
        try {
            const params = new URLSearchParams();
            if (sourceType !== "ALL")
                params.append("source_type", sourceType);
            if (statusFilter !== "ALL")
                params.append("status", statusFilter);
            const suffix = params.toString() ? `?${params.toString()}` : "";
            const res = await fetch(`/api/audit/logs${suffix}`, { method: "DELETE" });
            if (!res.ok) {
                throw new Error(`Clear logs failed: ${res.status}`);
            }
            prevLogIdsRef.current = new Set();
            setNewLogIds(new Set());
            setSelectedLog(null);
            await fetchLogs(false);
        }
        catch (err) {
            console.error("Failed to clear audit logs", err);
        }
        finally {
            setClearing(false);
        }
    }, [fetchLogs, sourceType, statusFilter, t]);
    const getStatusBadge = (status: string) => {
        const s = status.toUpperCase();
        if (s === "SUCCESS" || s === "COMPLETED")
            return <Badge variant="outline" className="text-green-500 bg-green-500/10 border-green-500/20">{s}</Badge>;
        if (s === "FAILED" || s === "ERROR")
            return <Badge variant="destructive" className="bg-red-500/10 text-red-500 border-red-500/20">{s}</Badge>;
        if (s === "SKIPPED")
            return <Badge variant="outline" className="text-yellow-500 bg-yellow-500/10 border-yellow-500/20">{s}</Badge>;
        if (s === "RUNNING")
            return <Badge variant="outline" className="text-blue-500 bg-blue-500/10 border-blue-500/20">{s}</Badge>;
        return <Badge variant="outline" className="text-muted-foreground">{s}</Badge>;
    };
    const getSourceTypeBadge = (type: string) => {
        const t = type.toUpperCase();
        if (t === "HOOK")
            return <Badge variant="outline" className="bg-purple-500/10 text-purple-600 border-purple-500/20">HOOK</Badge>;
        if (t === "CRON")
            return <Badge variant="outline" className="bg-orange-500/10 text-orange-600 border-orange-500/20">CRON</Badge>;
        return <Badge variant="secondary">{t}</Badge>;
    };
    return (<Card className="flex h-[min(76vh,760px)] min-h-0 flex-col overflow-hidden">
            <CardHeader className="flex flex-row items-center justify-between pb-4">
                <div>
                    <CardTitle className="text-lg flex items-center gap-2">
                        <Activity className="w-5 h-5 text-primary"/>
                        {t("components.memory.AuditLogsPanel.kb6caa3ba")}
                    </CardTitle>
                </div>
                <div className="flex items-center gap-4">
                    <Button variant="destructive" size="sm" onClick={() => void handleClearLogs()} disabled={loading || clearing}>
                        {clearing ? <Loader2 className="w-4 h-4 mr-2 animate-spin"/> : null}
                        {t("components.memory.AuditLogsPanel.k14f7b096")}
                    </Button>
                    <div className="flex items-center gap-2">
                        <Switch id="auto-refresh" checked={autoRefresh} onCheckedChange={setAutoRefresh}/>
                        <Label htmlFor="auto-refresh" className="text-xs text-muted-foreground cursor-pointer">
                            {autoRefresh ? `${t("components.memory.AuditLogsPanel.k075e1293")} (${countdown}s)` : t("components.memory.AuditLogsPanel.k075e1293")}
                        </Label>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => { fetchLogs(false); setCountdown(POLL_INTERVAL_MS / 1000); }} disabled={loading}>
                        <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`}/>
                        {t("components.memory.AuditLogsPanel.k876e8c06")}
                    </Button>
                </div>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col overflow-hidden">
                <div className="flex gap-4 mb-4">
                    <div className="w-[180px]">
                        <Select value={sourceType} onValueChange={setSourceType}>
                            <SelectTrigger>
                                <SelectValue placeholder={t("components.memory.AuditLogsPanel.k22120b28")}/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="ALL">{t("components.memory.AuditLogsPanel.k18a0d17c")}</SelectItem>
                                <SelectItem value="CRON">{t("components.memory.AuditLogsPanel.k222e3398")}</SelectItem>
                                <SelectItem value="HOOK">{t("components.memory.AuditLogsPanel.ka206d935")}</SelectItem>
                                <SelectItem value="SYSTEM">{t("components.memory.AuditLogsPanel.k9e8f3e84")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="w-[180px]">
                        <Select value={statusFilter} onValueChange={setStatusFilter}>
                            <SelectTrigger>
                                <SelectValue placeholder={t("components.memory.AuditLogsPanel.k03afb4ba")}/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="ALL">{t("components.memory.AuditLogsPanel.k110445a5")}</SelectItem>
                                <SelectItem value="SUCCESS">{t("components.memory.AuditLogsPanel.k0cbafd78")}</SelectItem>
                                <SelectItem value="FAILED">{t("components.memory.AuditLogsPanel.kb83c391f")}</SelectItem>
                                <SelectItem value="SKIPPED">{t("components.memory.AuditLogsPanel.k0d0c535a")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                </div>

                <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                    <Table className="table-fixed">
                        <TableHeader className="sticky top-0 z-10 bg-background">
                            <TableRow>
                                <TableHead className="w-[180px]">{t("components.memory.AuditLogsPanel.k88be2169")}</TableHead>
                                <TableHead className="w-[100px]">{t("components.memory.AuditLogsPanel.ke7139376")}</TableHead>
                                <TableHead className="w-[200px]">{t("components.memory.AuditLogsPanel.kb9050ab8")}</TableHead>
                                <TableHead className="w-[100px]">{t("components.memory.AuditLogsPanel.k1f59a61a")}</TableHead>
                                <TableHead>{t("components.memory.AuditLogsPanel.k4a1b9dfb")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {logs.length === 0 && !loading ? (<TableRow>
                                    <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                                        {t("components.memory.AuditLogsPanel.k2ce41752")}
                                    </TableCell>
                                </TableRow>) : (logs.map((log) => (<TableRow key={log.id} className={newLogIds.has(log.id) ? "bg-primary/5 border-l-2 border-l-primary" : ""}>
                                        <TableCell className="font-mono text-xs text-muted-foreground align-top">
                                            {formatLocalDateTime(log.timestamp)}
                                        </TableCell>
                                        <TableCell className="align-top">
                                            {getSourceTypeBadge(log.source_type)}
                                        </TableCell>
                                        <TableCell className="align-top font-medium text-sm break-words">
                                            {log.action}
                                        </TableCell>
                                        <TableCell className="align-top">
                                            {getStatusBadge(log.status)}
                                        </TableCell>
                                        <TableCell className="align-top text-xs text-muted-foreground">
                                            {log.details ? (<div className="space-y-2">
                                                    <p className="line-clamp-4 whitespace-pre-wrap break-words text-xs leading-5">
                                                        {log.details}
                                                    </p>
                                                    <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setSelectedLog(log)}>
                                                        {t("components.memory.AuditLogsPanel.k399e3198")}
                                                    </Button>
                                                </div>) : ("-")}
                                        </TableCell>
                                    </TableRow>)))}
                        </TableBody>
                    </Table>
                </div>
                
                {hasMore && (<div className="mt-4 flex justify-center">
                        <Button variant="secondary" size="sm" onClick={() => fetchLogs(true)} disabled={loading}>
                            {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin"/> : null}
                            {t("components.memory.AuditLogsPanel.kfc4dec15")}
                        </Button>
                    </div>)}
            </CardContent>
            <Dialog open={Boolean(selectedLog)} onOpenChange={(open) => { if (!open)
        setSelectedLog(null); }}>
                <DialogContent className="max-w-3xl">
                    <DialogHeader>
                        <DialogTitle>{t("components.memory.AuditLogsPanel.k9041b4bc")}</DialogTitle>
                    </DialogHeader>
                    {selectedLog ? (<div className="space-y-4">
                            <div className="grid gap-3 text-sm text-muted-foreground md:grid-cols-2">
                                <div>{t("components.memory.AuditLogsPanel.k62fb388c")}{formatLocalDateTime(selectedLog.timestamp)}</div>
                                <div>{t("components.memory.AuditLogsPanel.k925119c3")}{selectedLog.source_type}</div>
                                <div>{t("components.memory.AuditLogsPanel.kb0d81cd8")}{selectedLog.action}</div>
                                <div>{t("components.memory.AuditLogsPanel.kd12a1540")}{selectedLog.status}</div>
                            </div>
                            <div className="max-h-[420px] overflow-y-auto rounded-2xl border border-border bg-muted/30 p-4 text-sm leading-6 text-foreground">
                                <pre className="whitespace-pre-wrap break-words font-sans">{selectedLog.details || "-"}</pre>
                            </div>
                        </div>) : null}
                </DialogContent>
            </Dialog>
        </Card>);
}
