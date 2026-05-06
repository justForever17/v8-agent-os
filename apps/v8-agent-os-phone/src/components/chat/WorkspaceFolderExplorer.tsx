import { memo } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import type { WorkspaceFolderNode } from "@/src/types/admin";

function FolderRow({
    node,
    depth,
    selectedPath,
    expandedPaths,
    loadingPaths,
    onSelect,
    onToggle,
}: {
    node: WorkspaceFolderNode;
    depth: number;
    selectedPath: string;
    expandedPaths: Set<string>;
    loadingPaths: Set<string>;
    onSelect: (node: WorkspaceFolderNode) => void;
    onToggle: (node: WorkspaceFolderNode) => void;
}) {
    const { colors } = useUiPrefs();
    const expanded = expandedPaths.has(node.path);
    const selected = selectedPath === node.path;
    const loading = loadingPaths.has(node.path);
    const children = node.children || [];

    return (
        <View>
            <View
                style={[
                    styles.row,
                    {
                        paddingLeft: 8 + (depth * 18),
                        backgroundColor: selected ? colors.primarySoft : "transparent",
                        borderColor: selected ? `${colors.primary}44` : "transparent",
                    },
                ]}
            >
                <Pressable style={styles.toggle} onPress={() => onToggle(node)} hitSlop={8}>
                    {loading ? (
                        <ActivityIndicator size="small" color={colors.textMuted} />
                    ) : (
                        <MaterialCommunityIcons
                            name={expanded ? "chevron-down" : "chevron-right"}
                            size={16}
                            color={colors.textMuted}
                        />
                    )}
                </Pressable>
                <Pressable style={styles.rowMain} onPress={() => onSelect(node)}>
                    <MaterialCommunityIcons name="folder-outline" size={16} color={selected ? colors.primary : colors.textMuted} />
                    <View style={styles.rowTextWrap}>
                        <Text numberOfLines={1} style={[styles.rowTitle, { color: selected ? colors.primaryDeep : colors.text }]}>
                            {node.name || node.path}
                        </Text>
                        <Text numberOfLines={1} style={[styles.rowMeta, { color: colors.textMuted }]}>
                            {node.path}
                        </Text>
                    </View>
                </Pressable>
            </View>
            {expanded ? children.map((child) => (
                <FolderRow
                    key={child.path}
                    node={child}
                    depth={depth + 1}
                    selectedPath={selectedPath}
                    expandedPaths={expandedPaths}
                    loadingPaths={loadingPaths}
                    onSelect={onSelect}
                    onToggle={onToggle}
                />
            )) : null}
        </View>
    );
}

export const WorkspaceFolderExplorer = memo(function WorkspaceFolderExplorer({
    roots,
    selectedPath,
    expandedPaths,
    loadingPaths,
    onSelect,
    onToggle,
    emptyLabel,
}: {
    roots: WorkspaceFolderNode[];
    selectedPath: string;
    expandedPaths: Set<string>;
    loadingPaths: Set<string>;
    onSelect: (node: WorkspaceFolderNode) => void;
    onToggle: (node: WorkspaceFolderNode) => void;
    emptyLabel: string;
}) {
    const { colors } = useUiPrefs();
    return (
        <View style={[styles.wrap, { backgroundColor: colors.surface, borderColor: colors.border }]}>
            <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator>
                <ScrollView style={styles.verticalScroll} nestedScrollEnabled showsVerticalScrollIndicator>
                    <View style={styles.content}>
                        {roots.length ? roots.map((root) => (
                            <FolderRow
                                key={root.path}
                                node={root}
                                depth={0}
                                selectedPath={selectedPath}
                                expandedPaths={expandedPaths}
                                loadingPaths={loadingPaths}
                                onSelect={onSelect}
                                onToggle={onToggle}
                            />
                        )) : (
                            <Text style={[styles.emptyText, { color: colors.textMuted }]}>{emptyLabel}</Text>
                        )}
                    </View>
                </ScrollView>
            </ScrollView>
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        maxHeight: 280,
        minHeight: 170,
        borderWidth: 1,
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    verticalScroll: {
        maxHeight: 280,
    },
    content: {
        minWidth: 520,
        paddingVertical: 6,
    },
    row: {
        minHeight: 44,
        flexDirection: "row",
        alignItems: "center",
        borderWidth: 1,
        borderRadius: 12,
        marginHorizontal: 6,
        marginVertical: 2,
    },
    toggle: {
        width: 28,
        height: 36,
        alignItems: "center",
        justifyContent: "center",
    },
    rowMain: {
        flex: 1,
        minWidth: 0,
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        paddingRight: 10,
    },
    rowTextWrap: {
        flex: 1,
        minWidth: 0,
    },
    rowTitle: {
        fontSize: 13,
        fontWeight: "800",
    },
    rowMeta: {
        marginTop: 2,
        fontSize: 10,
        fontWeight: "600",
    },
    emptyText: {
        padding: 14,
        fontSize: 12,
        fontWeight: "700",
    },
});
