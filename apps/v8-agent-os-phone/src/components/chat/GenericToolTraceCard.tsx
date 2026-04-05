import { memo } from "react";
import { StyleSheet, View } from "react-native";

import { ToolCard, type ToolInvocation } from "@/src/components/chat/ToolCard";

export const GenericToolTraceCard = memo(function GenericToolTraceCard({
    toolInvocation,
}: {
    toolInvocation: ToolInvocation;
}) {
    return (
        <View style={styles.wrap}>
            <ToolCard toolInvocation={toolInvocation} />
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        opacity: 0.68,
    },
});
