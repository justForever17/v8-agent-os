
"use client"

import {
    Toast,
    ToastClose,
    ToastDescription,
    ToastProvider,
    ToastTitle,
    ToastViewport,
} from "@admin/components/ui/toast"
import { useToast } from "@admin/components/ui/use-toast"
import { useResolveText } from "@admin/components/providers/LocaleProvider"
import { isTranslationKey } from "@admin/lib/locale"

export function Toaster() {
    const { toasts } = useToast()
    const resolveText = useResolveText()

    return (
        <ToastProvider>
            {toasts.map(function ({ id, title, description, action, ...props }) {
                return (
                    <Toast key={id} {...props}>
                        <div className="grid gap-1">
                            {title && <ToastTitle>{typeof title === "string" && isTranslationKey(title) ? resolveText(title) : title}</ToastTitle>}
                            {description && (
                                <ToastDescription>{typeof description === "string" && isTranslationKey(description) ? resolveText(description) : description}</ToastDescription>
                            )}
                        </div>
                        {action}
                        <ToastClose />
                    </Toast>
                )
            })}
            <ToastViewport />
        </ToastProvider>
    )
}
