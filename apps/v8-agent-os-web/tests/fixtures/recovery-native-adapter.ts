export { Pressable, Text, TextInput, View } from "react-native-web";
export const Alert = {
    alert: (_title: string, description: string, buttons: Array<{ style?: string; onPress?: () => void }>) => {
        (window as any).phoneConfirm = { description, confirm: () => buttons.find((button) => button.style === "destructive")?.onPress?.() };
    },
};
