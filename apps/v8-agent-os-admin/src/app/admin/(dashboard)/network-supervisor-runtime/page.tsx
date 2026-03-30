import { readCanonicalConfigDiagnostics } from "@/lib/server/bridge-config";
import { NetworkSupervisorRuntimeWorkbench } from "@/components/network-supervisor/NetworkSupervisorRuntimeWorkbench";

export default function NetworkSupervisorRuntimePage() {
    const bridgeDiagnostics = readCanonicalConfigDiagnostics();
    return <NetworkSupervisorRuntimeWorkbench bridgeDiagnostics={bridgeDiagnostics} />;
}
