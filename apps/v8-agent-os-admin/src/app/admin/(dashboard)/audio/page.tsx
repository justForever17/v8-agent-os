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
import { lt } from "@/lib/locale";

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
                title: t(lt("配置已保存", "Saved")),
                description: t(lt("多模态语音配置更新成功，并在引擎侧重载生效。", "Audio settings were updated and reloaded in the engine.")),
            });
        } catch {
            toast({
                title: t(lt("保存失败", "Save failed")),
                description: t(lt("请检查引擎连通性", "Please check engine connectivity.")),
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

    if (loading) return <div className="p-8">{t(lt("正在载入音频配置...", "Loading audio config..."))}</div>;

    return (
        <div className="p-8 max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold flex items-center gap-2">
                        <Mic className="h-8 w-8 text-primary" />
                        {t(lt("多模挂载配置", "Audio"))}
                    </h1>
                    <p className="text-muted-foreground mt-2">
                        {t(lt("为 V8 Agent OS 指派语音识别 (STT) 和语音合成 (TTS) 的服务提供商。", "Assign speech-to-text and text-to-speech providers for V8 Agent OS."))}
                    </p>
                </div>
                <Button onClick={handleSave} disabled={saving} className="gap-2">
                    <Save className="h-4 w-4" />
                    {saving ? t(lt("保存中...", "Saving...")) : t(lt("保存配置", "Save"))}
                </Button>
            </div>

            <div className="grid gap-6">
                {/* STT Configuration */}
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle>{t(lt("语音识别 (STT)", "Speech to Text"))}</CardTitle>
                                <CardDescription>{t(lt("配置用户的语音输入转写服务。", "Configure the user speech transcription service."))}</CardDescription>
                            </div>
                            <div className="w-48">
                                <Select 
                                    value={config.stt.active_provider} 
                                    onValueChange={(val) => setConfig(prev => ({...prev, stt: {...prev.stt, active_provider: val}}))}
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder={t(lt("选择提供商", "Select provider"))} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="custom">{t(lt("自建 / 第三方 API", "Custom / 3rd-party API"))}</SelectItem>
                                        <SelectItem value="baidu">{t(lt("百度智能云 STT", "Baidu STT"))}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {config.stt.active_provider === "custom" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t(lt("自建服务 (Custom API)", "Custom API"))}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t(lt("兼容返回纯文本的 HTTP 接口，详见自建服务对接文档。", "Compatible with HTTP endpoints that return plain text output."))}</p>
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
                                    <Label>{t(lt("API Key (可选)", "API Key (optional)"))}</Label>
                                    <Input
                                        type="password"
                                        value={config.stt.providers.custom.api_key || ""}
                                        onChange={e => updateSttValue("custom", "api_key", e.target.value)}
                                        placeholder={t(lt("如需鉴权可填写", "Only needed if your endpoint requires auth"))}
                                    />
                                </div>
                            </div>
                        )}

                        {config.stt.active_provider === "baidu" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t(lt("百度智能云 STT", "Baidu STT"))}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t(lt("使用极速版短语音识别服务，获取你的 API Key 和 Secret Key。", "Use Baidu short-form speech recognition with your API Key and Secret Key."))}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("App ID (暂未强制, 可选)", "App ID (optional)"))}</Label>
                                    <Input 
                                        value={config.stt.providers.baidu.app_id || ""} 
                                        onChange={e => updateSttValue("baidu", "app_id", e.target.value)} 
                                        placeholder={t(lt("填写你的 APP_ID", "Enter your APP_ID"))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>API Key</Label>
                                    <Input 
                                        value={config.stt.providers.baidu.api_key || ""} 
                                        onChange={e => updateSttValue("baidu", "api_key", e.target.value)} 
                                        placeholder={t(lt("填写你的 API_KEY", "Enter your API_KEY"))}
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
                                <CardTitle>{t(lt("语音合成 (TTS)", "Text to Speech"))}</CardTitle>
                                <CardDescription>{t(lt("配置系统助手向用户播报的语音服务。", "Configure the assistant voice playback service."))}</CardDescription>
                            </div>
                            <div className="w-48">
                                <Select 
                                    value={config.tts.active_provider} 
                                    onValueChange={(val) => setConfig(prev => ({...prev, tts: {...prev.tts, active_provider: val}}))}
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder={t(lt("选择提供商", "Select provider"))} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="edge-tts">{t(lt("微软 Edge TTS (免费)", "Microsoft Edge TTS (free)"))}</SelectItem>
                                        <SelectItem value="custom">{t(lt("自建 / 第三方 API", "Custom / 3rd-party API"))}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {config.tts.active_provider === "edge-tts" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t(lt("微软 Edge TTS", "Microsoft Edge TTS"))}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t(lt("无配额限制的免费高质量合成，支持流式输出。", "High-quality free synthesis with streaming output."))}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("默认发音人 (Voice)", "Default voice"))}</Label>
                                    <Select 
                                        value={config.tts.edge_tts.voice || "zh-CN-XiaoxiaoNeural"} 
                                        onValueChange={(val) => updateTtsValue("edge_tts", "voice", val)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder={t(lt("选择音色...", "Choose a voice..."))} />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="zh-CN-XiaoxiaoNeural">{t(lt("晓晓 (女声, 温柔亲切)", "Xiaoxiao (warm female)"))}</SelectItem>
                                            <SelectItem value="zh-CN-YunxiNeural">{t(lt("云希 (活力男声)", "Yunxi (energetic male)"))}</SelectItem>
                                            <SelectItem value="zh-CN-YunjianNeural">{t(lt("云健 (成熟男声)", "Yunjian (steady male)"))}</SelectItem>
                                            <SelectItem value="zh-CN-XiaoyiNeural">{t(lt("晓伊 (可爱女童声)", "Xiaoyi (young female)"))}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                        )}

                        {config.tts.active_provider === "custom" && (
                            <div className="space-y-4 border rounded-md p-4 bg-muted/30">
                                <div>
                                    <Label className="text-sm font-semibold text-primary">{t(lt("自建服务 (Custom API)", "Custom API"))}</Label>
                                    <p className="text-xs text-muted-foreground mb-4">{t(lt("使用 HTTP GET 接口流式输出 mp3，如 ChatTTS 一键部署接口等。", "Uses an HTTP GET endpoint that streams MP3, such as a one-click ChatTTS deployment."))}</p>
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
                                    <Label>{t(lt("API Key (可选)", "API Key (optional)"))}</Label>
                                    <Input
                                        type="password"
                                        value={config.tts.custom.api_key || ""}
                                        onChange={e => updateTtsValue("custom", "api_key", e.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("Voice (可选)", "Voice (optional)"))}</Label>
                                    <Input
                                        value={config.tts.custom.voice || ""}
                                        onChange={e => updateTtsValue("custom", "voice", e.target.value)}
                                        placeholder={t(lt("如 provider 支持 voice 字段可填写", "Fill this if your provider supports a voice field"))}
                                    />
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
            
            <p className="text-sm text-muted-foreground mt-4 text-center">
                {t(lt("配置保存在 ~/.v8-agent-os/config.json 的 audio 域", "Settings are stored in the audio section of ~/.v8-agent-os/config.json"))}
            </p>
        </div>
    );
}
