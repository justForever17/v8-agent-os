"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Mic, Save } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";

export default function AudioPage() {
    const { toast } = useToast();
    const t = useT();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const [config, setConfig] = useState({
        stt: {
            active_provider: "baidu",
            providers: {
                custom: { endpoint: "", api_key: "" },
                baidu: { app_id: "", api_key: "", secret_key: "" }
            }
        },
        tts: {
            active_provider: "edge-tts",
            edge_tts: { voice: "zh-CN-XiaoxiaoNeural", rate: "+0%", volume: "+0%" },
            custom: { endpoint: "", api_key: "", voice: "" }
        }
    });

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await fetch("/api/audio/config");
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => {
                    // Deep merge to ensure all defaults exist
                    return {
                        stt: {
                            ...prev.stt,
                            ...(data.stt || {}),
                            providers: {
                                ...prev.stt.providers,
                                ...(data.stt?.providers || {})
                            }
                        },
                        tts: {
                            ...prev.tts,
                            ...(data.tts || {}),
                        }
                    };
                });
            }
        } catch (error) {
            console.error("Failed to load configs", error);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const res = await fetch("/api/audio/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(config)
            });
            if (!res.ok) throw new Error("Save failed");
            toast({
                title: t("app.admin.dashboard.audio.page.k1cf22082"),
                description: t("app.admin.dashboard.audio.page.k27f5bb98"),
            });
        } catch {
            toast({
                title: t("app.admin.dashboard.audio.page.k12769ce1"),
                description: t("app.admin.dashboard.audio.page.k42aa2f80"),
                variant: "destructive"
            });
        } finally {
            setSaving(false);
        }
    };

    const updateSttValue = (provider: "custom" | "baidu", key: string, value: string) => {
        setConfig(prev => ({
            ...prev,
            stt: {
                ...prev.stt,
                providers: {
                    ...prev.stt.providers,
                    [provider]: {
                        ...prev.stt.providers[provider],
                        [key]: value
                    }
                }
            }
        }));
    };

    const updateTtsValue = (provider: "edge_tts" | "custom", key: string, value: string) => {
        setConfig(prev => ({
            ...prev,
            tts: {
                ...prev.tts,
                [provider]: {
                    ...prev.tts[provider],
                    [key]: value
                }
            }
        }));
    };

    if (loading) return <div className="p-8">{t("app.admin.dashboard.audio.page.k6d76de89")}</div>;

    return (
        <div className="p-8 max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold flex items-center gap-2">
                        <Mic className="h-8 w-8 text-primary" />
                        {t("app.admin.dashboard.audio.page.k5c98feb1")}
                    </h1>
                    <p className="text-muted-foreground mt-2">
                        {t("app.admin.dashboard.audio.page.k918e6e58")}
                    </p>
                </div>
                <Button onClick={handleSave} disabled={saving} className="gap-2">
                    <Save className="h-4 w-4" />
                    {saving ? t("app.admin.dashboard.audio.page.kc225e8a3") : t("app.admin.dashboard.audio.page.ke22cbc80")}
                </Button>
            </div>

            <div className="grid gap-6">
                {/* STT Configuration */}
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle>{t("app.admin.dashboard.audio.page.k1593b15d")}</CardTitle>
                                <CardDescription>{t("app.admin.dashboard.audio.page.kc407ee87")}</CardDescription>
                            </div>
                            <div className="w-48">
                                <Select 
                                    value={config.stt.active_provider} 
                                    onValueChange={(val) => setConfig(prev => ({...prev, stt: {...prev.stt, active_provider: val}}))}
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder={t("app.admin.dashboard.audio.page.k2deec296")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="custom">{t("app.admin.dashboard.audio.page.k49861c04")}</SelectItem>
                                        <SelectItem value="baidu">{t("app.admin.dashboard.audio.page.k347cd25b")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {config.stt.active_provider === "custom" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t("app.admin.dashboard.audio.page.k1c5aaed3")}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t("app.admin.dashboard.audio.page.k50df78b0")}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>API URL</Label>
                                    <Input 
                                        value={config.stt.providers.custom.endpoint || ""} 
                                        onChange={e => updateSttValue("custom", "endpoint", e.target.value)} 
                                        placeholder="http://127.0.0.1:5000/transcribe" 
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.audio.page.kb8695ac0")}</Label>
                                    <Input
                                        type="password"
                                        value={config.stt.providers.custom.api_key || ""}
                                        onChange={e => updateSttValue("custom", "api_key", e.target.value)}
                                        placeholder={t("app.admin.dashboard.audio.page.k99b36d78")}
                                    />
                                </div>
                            </div>
                        )}

                        {config.stt.active_provider === "baidu" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t("app.admin.dashboard.audio.page.k347cd25b")}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t("app.admin.dashboard.audio.page.k696912b7")}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.audio.page.k004a31eb")}</Label>
                                    <Input 
                                        value={config.stt.providers.baidu.app_id || ""} 
                                        onChange={e => updateSttValue("baidu", "app_id", e.target.value)} 
                                        placeholder={t("app.admin.dashboard.audio.page.kdbb863db")}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>API Key</Label>
                                    <Input 
                                        value={config.stt.providers.baidu.api_key || ""} 
                                        onChange={e => updateSttValue("baidu", "api_key", e.target.value)} 
                                        placeholder={t("app.admin.dashboard.audio.page.k19b48365")}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>Secret Key</Label>
                                    <Input 
                                        type="password"
                                        value={config.stt.providers.baidu.secret_key || ""} 
                                        onChange={e => updateSttValue("baidu", "secret_key", e.target.value)} 
                                    />
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* TTS Configuration */}
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle>{t("app.admin.dashboard.audio.page.k7c12ec6b")}</CardTitle>
                                <CardDescription>{t("app.admin.dashboard.audio.page.kfc48901d")}</CardDescription>
                            </div>
                            <div className="w-48">
                                <Select 
                                    value={config.tts.active_provider} 
                                    onValueChange={(val) => setConfig(prev => ({...prev, tts: {...prev.tts, active_provider: val}}))}
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder={t("app.admin.dashboard.audio.page.k2deec296")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="edge-tts">{t("app.admin.dashboard.audio.page.kc0f20f31")}</SelectItem>
                                        <SelectItem value="custom">{t("app.admin.dashboard.audio.page.k49861c04")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {config.tts.active_provider === "edge-tts" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t("app.admin.dashboard.audio.page.k442c44b3")}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t("app.admin.dashboard.audio.page.ke0191398")}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.audio.page.k357ab02c")}</Label>
                                    <Select 
                                        value={config.tts.edge_tts.voice || "zh-CN-XiaoxiaoNeural"} 
                                        onValueChange={(val) => updateTtsValue("edge_tts", "voice", val)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder={t("app.admin.dashboard.audio.page.k598128a3")} />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="zh-CN-XiaoxiaoNeural">{t("app.admin.dashboard.audio.page.k0f6ca09d")}</SelectItem>
                                            <SelectItem value="zh-CN-YunxiNeural">{t("app.admin.dashboard.audio.page.k366a2c45")}</SelectItem>
                                            <SelectItem value="zh-CN-YunjianNeural">{t("app.admin.dashboard.audio.page.k31f2a1a4")}</SelectItem>
                                            <SelectItem value="zh-CN-XiaoyiNeural">{t("app.admin.dashboard.audio.page.k80358ca2")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                        )}

                        {config.tts.active_provider === "custom" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t("app.admin.dashboard.audio.page.k1c5aaed3")}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t("app.admin.dashboard.audio.page.k29a8ce90")}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>API URL</Label>
                                    <Input 
                                        value={config.tts.custom.endpoint || ""} 
                                        onChange={e => updateTtsValue("custom", "endpoint", e.target.value)} 
                                        placeholder="http://127.0.0.1:8080/tts"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.audio.page.kb8695ac0")}</Label>
                                    <Input
                                        type="password"
                                        value={config.tts.custom.api_key || ""}
                                        onChange={e => updateTtsValue("custom", "api_key", e.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.audio.page.kf142c067")}</Label>
                                    <Input
                                        value={config.tts.custom.voice || ""}
                                        onChange={e => updateTtsValue("custom", "voice", e.target.value)}
                                        placeholder={t("app.admin.dashboard.audio.page.k8a4597ac")}
                                    />
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
            
            <p className="text-sm text-muted-foreground mt-4 text-center">
                {t("app.admin.dashboard.audio.page.kf9563d3e")}
            </p>
        </div>
    );
}
