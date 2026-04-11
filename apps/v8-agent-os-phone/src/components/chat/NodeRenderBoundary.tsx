import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { radii, spacing } from "@/src/theme/tokens";

type Props = {
    title: string;
    description: string;
    borderColor: string;
    backgroundColor: string;
    titleColor: string;
    textColor: string;
    children: React.ReactNode;
};

type State = {
    hasError: boolean;
};

export class NodeRenderBoundary extends React.PureComponent<Props, State> {
    state: State = {
        hasError: false,
    };

    static getDerivedStateFromError() {
        return { hasError: true };
    }

    componentDidUpdate(prevProps: Props) {
        if (prevProps.children !== this.props.children && this.state.hasError) {
            this.setState({ hasError: false });
        }
    }

    render() {
        if (!this.state.hasError) {
            return this.props.children;
        }
        return (
            <View
                style={[
                    styles.fallback,
                    {
                        borderColor: this.props.borderColor,
                        backgroundColor: this.props.backgroundColor,
                    },
                ]}
            >
                <MaterialCommunityIcons name="alert-circle-outline" size={16} color={this.props.titleColor} />
                <View style={styles.fallbackBody}>
                    <Text style={[styles.fallbackTitle, { color: this.props.titleColor }]}>{this.props.title}</Text>
                    <Text style={[styles.fallbackText, { color: this.props.textColor }]}>{this.props.description}</Text>
                </View>
            </View>
        );
    }
}

const styles = StyleSheet.create({
    fallback: {
        width: "100%",
        flexDirection: "row",
        alignItems: "flex-start",
        gap: spacing.sm,
        borderWidth: 1,
        borderRadius: radii.lg,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    fallbackBody: {
        flex: 1,
        gap: 2,
    },
    fallbackTitle: {
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "700",
    },
    fallbackText: {
        fontSize: 11,
        lineHeight: 16,
    },
});
