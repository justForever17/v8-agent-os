import { readCanonicalConfigDiagnostics } from "@admin/lib/server/bridge-config";
import { NetworkSupervisorRuntimeWorkbench } from "@admin/components/network-supervisor/NetworkSupervisorRuntimeWorkbench";

export default function NetworkSupervisorRuntimePage() {
    const bridgeDiagnostics = readCanonicalConfigDiagnostics();
    return <NetworkSupervisorRuntimeWorkbench bridgeDiagnostics={bridgeDiagnostics} />;
}
