"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, RefreshCw, Trash2, Play, Pause } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/components/providers/LocaleProvider";

interface MusicTrack {
    id: string;
    title: string;
    url: string;
    isEnabled: boolean;
    order: number;
}

export default function MusicPage() {
    const t = useT();
    const [tracks, setTracks] = useState<MusicTrack[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [playingPreview, setPlayingPreview] = useState<string | null>(null);
    const [audioElement, setAudioElement] = useState<HTMLAudioElement | null>(null);

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        try {
            // Fetch all tracks (we might need to update the GET endpoint to return all, not just enabled)
            // For now, let's assume the GET endpoint returns what we need or we filter on client
            const res = await fetch("/api/music");
            if (res.ok) {
                setTracks(await res.json());
            }
        } catch (error) {
            console.error("Failed to fetch tracks", error);
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchData();
        return () => {
            if (audioElement) {
                audioElement.pause();
            }
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fetchData]); // audioElement is not needed in deps here as it's a cleanup function

    const handleSave = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        const data = {
            title: formData.get("title"),
            url: formData.get("url"),
        };

        try {
            const res = await fetch("/api/music", {
                method: "POST",
                body: JSON.stringify(data),
                headers: { "Content-Type": "application/json" }
            });

            if (res.ok) {
                setIsDialogOpen(false);
                fetchData();
            } else {
                alert(t("app.admin.dashboard.music.page.alert.addFailed"));
            }
        } catch {
            alert(t("app.admin.dashboard.music.page.alert.addError"));
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm(t("app.admin.dashboard.music.page.confirm.delete"))) return;
        try {
            await fetch(`/api/music/${id}`, { method: "DELETE" });
            fetchData();
        } catch (error) {
            console.error("Failed to delete track", error);
        }
    };

    const handleToggle = async (id: string, currentState: boolean) => {
        try {
            await fetch(`/api/music/${id}`, {
                method: "PUT",
                body: JSON.stringify({ isEnabled: !currentState }),
                headers: { "Content-Type": "application/json" }
            });
            fetchData();
        } catch (error) {
            console.error("Failed to update track", error);
        }
    };

    const togglePreview = (url: string) => {
        if (playingPreview === url) {
            audioElement?.pause();
            setPlayingPreview(null);
        } else {
            if (audioElement) audioElement.pause();
            const audio = new Audio(url);
            audio.play();
            audio.onended = () => setPlayingPreview(null);
            setAudioElement(audio);
            setPlayingPreview(url);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">{t("app.admin.dashboard.music.page.title")}</h1>
                    <p className="text-muted-foreground">{t("app.admin.dashboard.music.page.description")}</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={fetchData} disabled={isLoading}>
                        <RefreshCw className={`w-4 h-4 mr-2 ${isLoading ? "animate-spin" : ""}`} />
                        {t("app.admin.dashboard.music.page.refresh")}
                    </Button>
                    <Button onClick={() => setIsDialogOpen(true)}>
                        <Plus className="w-4 h-4 mr-2" />
                        {t("app.admin.dashboard.music.page.add")}
                    </Button>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>{t("app.admin.dashboard.music.page.list.title")}</CardTitle>
                    <CardDescription>{t("app.admin.dashboard.music.page.list.description")}</CardDescription>
                </CardHeader>
                <CardContent>
                    {tracks.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                            {t("app.admin.dashboard.music.page.list.empty")}
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.music.page.table.title")}</TableHead>
                                    <TableHead>URL</TableHead>
                                    <TableHead>{t("app.admin.dashboard.music.page.table.status")}</TableHead>
                                    <TableHead className="text-right">{t("app.admin.dashboard.music.page.table.actions")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {tracks.map((track) => (
                                    <TableRow key={track.id}>
                                        <TableCell className="font-medium">{track.title}</TableCell>
                                        <TableCell className="max-w-[300px] truncate text-muted-foreground text-xs">
                                            {track.url}
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-2">
                                                <Switch
                                                    checked={track.isEnabled}
                                                    onCheckedChange={() => handleToggle(track.id, track.isEnabled)}
                                                />
                                                <span className="text-sm text-muted-foreground">
                                                    {track.isEnabled
                                                        ? t("app.admin.dashboard.music.page.status.enabled")
                                                        : t("app.admin.dashboard.music.page.status.disabled")}
                                                </span>
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Button variant="ghost" size="icon" onClick={() => togglePreview(track.url)}>
                                                {playingPreview === track.url ? (
                                                    <Pause className="w-4 h-4" />
                                                ) : (
                                                    <Play className="w-4 h-4" />
                                                )}
                                            </Button>
                                            <Button variant="ghost" size="icon" className="text-destructive" onClick={() => handleDelete(track.id)}>
                                                <Trash2 className="w-4 h-4" />
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{t("app.admin.dashboard.music.page.dialog.title")}</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleSave} className="space-y-6">
                        <div className="space-y-2">
                            <Label htmlFor="title">{t("app.admin.dashboard.music.page.field.title")}</Label>
                            <Input id="title" name="title" required placeholder="app.admin.dashboard.music.page.k29e0fa0e" />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="url">{t("app.admin.dashboard.music.page.field.url")}</Label>
                            <Input id="url" name="url" required placeholder="https://example.com/music.mp3" />
                        </div>
                        <Button type="submit" className="w-full">{t("app.admin.dashboard.music.page.save")}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
}
