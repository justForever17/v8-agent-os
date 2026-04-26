import Link from "next/link";
import { Music2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function LegacyMusicPage() {
    return (
        <div className="mx-auto max-w-3xl space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Legacy Background Music</h1>
                <p className="mt-2 text-muted-foreground">
                    旧音乐 URL 播放器配置已退位，仅保留兼容 API。新的音乐创作、配乐 brief、cue sheet 和音乐引用请进入 Creative Media Runtime。
                </p>
            </div>

            <Card className="border-violet-200 bg-violet-50/70">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Music2 className="h-5 w-5" />
                        边界说明
                    </CardTitle>
                    <CardDescription>
                        `/api/music` 与 `MusicTrack` 只服务历史播放兼容，不再作为创意媒体音乐资产的配置入口。
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4 text-sm text-slate-700">
                    <div className="rounded-md border bg-white p-3">Creative Media 音乐不会写入旧播放器曲库。</div>
                    <div className="rounded-md border bg-white p-3">未来生成或引用的音乐音频必须进入 artifact / asset ledger。</div>
                    <Button asChild>
                        <Link href="/admin/creative-media">
                            <Sparkles className="mr-2 h-4 w-4" />
                            打开 Creative Media Runtime
                        </Link>
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}
