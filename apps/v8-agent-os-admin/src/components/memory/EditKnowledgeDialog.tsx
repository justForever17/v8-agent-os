"use client";

import { useState, useEffect } from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loader2 } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface KnowledgeItem {
    id: string;
    fact: string;
    category: string;
    scope: string;
    [key: string]: unknown;
}

interface EditKnowledgeDialogProps {
    item: KnowledgeItem | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSave: (id: string, updated: { fact: string; category: string; scope: string }) => Promise<void>;
}

export function EditKnowledgeDialog({ item, open, onOpenChange, onSave }: EditKnowledgeDialogProps) {
    const t = useT();
    const [fact, setFact] = useState("");
    const [category, setCategory] = useState("");
    const [scope, setScope] = useState("");
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (item) {
            setFact(item.fact);
            setCategory(item.category ?? "");
            setScope(item.scope ?? "");
        }
    }, [item]);

    const handleSave = async () => {
        if (!item || !fact.trim()) return;
        setSaving(true);
        try {
            await onSave(item.id, { fact: fact.trim(), category, scope });
            onOpenChange(false);
        } finally {
            setSaving(false);
        }
    };

    const hasChanges = item && (
        fact !== item.fact || category !== item.category || scope !== item.scope
    );

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-2xl">
                <DialogHeader>
                    <DialogTitle>{t("编辑知识条目")}</DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="space-y-1">
                        <Label className="text-xs text-muted-foreground">{t("ID（只读）")}</Label>
                        <p className="text-xs font-mono text-muted-foreground/60 px-3 py-1.5 rounded bg-muted/30 border">
                            {item?.id ?? "—"}
                        </p>
                    </div>

                    <div className="space-y-1">
                        <Label htmlFor="fact">{t("知识内容（全文）")}</Label>
                        <Textarea
                            id="fact"
                            value={fact}
                            onChange={(e) => setFact(e.target.value)}
                            rows={5}
                            className="resize-none font-mono text-sm leading-relaxed"
                            placeholder={t("请输入知识内容...")}
                        />
                        <p className="text-xs text-muted-foreground text-right">
                            {fact.length} {t("字符")}
                        </p>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1">
                            <Label htmlFor="scope">{t(lt("范围", "Scope"))}</Label>
                            <Input
                                id="scope"
                                value={scope}
                                onChange={(e) => setScope(e.target.value)}
                                placeholder={t(lt("如: global / project:v8-agent-os", "e.g. global / project:v8-agent-os"))}
                                className="font-mono text-sm"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="category">{t(lt("类别", "Category"))}</Label>
                            <Input
                                id="category"
                                value={category}
                                onChange={(e) => setCategory(e.target.value)}
                                placeholder={t(lt("如: Architecture / Preference", "e.g. Architecture / Preference"))}
                                className="text-sm"
                            />
                        </div>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                        {t("取消")}
                    </Button>
                    <Button onClick={handleSave} disabled={saving || !hasChanges}>
                        {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                        {t("保存修改")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
