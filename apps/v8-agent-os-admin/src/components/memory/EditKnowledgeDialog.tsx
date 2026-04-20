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
                    <DialogTitle>{t("components.memory.EditKnowledgeDialog.kdfab6846")}</DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="space-y-1">
                        <Label className="text-xs text-muted-foreground">{t("components.memory.EditKnowledgeDialog.k351424ba")}</Label>
                        <p className="text-xs font-mono text-muted-foreground/60 px-3 py-1.5 rounded bg-muted/30 border">
                            {item?.id ?? "—"}
                        </p>
                    </div>

                    <div className="space-y-1">
                        <Label htmlFor="fact">{t("components.memory.EditKnowledgeDialog.k6c3f5dcb")}</Label>
                        <Textarea
                            id="fact"
                            value={fact}
                            onChange={(e) => setFact(e.target.value)}
                            rows={5}
                            className="resize-none font-mono text-sm leading-relaxed"
                            placeholder={t("components.memory.EditKnowledgeDialog.kd4a6ba76")}
                        />
                        <p className="text-xs text-muted-foreground text-right">
                            {fact.length} {t("components.memory.EditKnowledgeDialog.k392efcce")}
                        </p>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1">
                            <Label htmlFor="scope">{t("components.memory.EditKnowledgeDialog.kf50bdadf")}</Label>
                            <Input
                                id="scope"
                                value={scope}
                                onChange={(e) => setScope(e.target.value)}
                                placeholder={t("components.memory.EditKnowledgeDialog.k898e26cf")}
                                className="font-mono text-sm"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="category">{t("components.memory.EditKnowledgeDialog.kd21c1ce0")}</Label>
                            <Input
                                id="category"
                                value={category}
                                onChange={(e) => setCategory(e.target.value)}
                                placeholder={t("components.memory.EditKnowledgeDialog.ka255d0d0")}
                                className="text-sm"
                            />
                        </div>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                        {t("components.memory.EditKnowledgeDialog.kb92cb20c")}
                    </Button>
                    <Button onClick={handleSave} disabled={saving || !hasChanges}>
                        {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                        {t("components.memory.EditKnowledgeDialog.k1a3a9893")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
