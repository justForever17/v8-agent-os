package expo.modules.v8executor

/** Pure deterministic admission, shared by the native dispatcher and JVM fault tests. */
data class CommandIdentity(
  val authorityId: String, val deviceId: String, val bootId: String, val controlSessionId: String,
  val leaseEpoch: Long, val grantRevision: Long, val capabilityRevision: Long,
  val issuedUnixMs: Long, val deadlineUnixMs: Long, val ttlMs: Long,
  val capability: String, val resourceId: String
)

class CommandGuard(val authorityId: String, val deviceId: String, val bootId: String, previousEpoch: Long = 0L, previousGrantRevision: Long = 0L) {
  var controlSessionId = ""
    private set
  var enabled = false
    private set
  var leaseEpoch = previousEpoch
    private set
  var grantRevision = previousGrantRevision
    private set
  private var leaseUntil = 0L
  private var anchorUnix = 0L
  private var anchorMono = 0L
  private var grants = emptySet<Pair<String, String>>()
  var localApps = emptySet<String>()

  fun arm(session: String) { require(session.isNotBlank()); enabled = true; controlSessionId = session; leaseUntil = 0 }
  fun stop() { enabled = false; controlSessionId = ""; leaseUntil = 0; grants = emptySet() }
  fun disconnect() { leaseUntil = 0 }
  fun hasGrant(capability: String, resource: String) = (capability to resource) in grants

  fun lease(epoch: Long, revision: Long, serverUnix: Long, expiresUnix: Long, nowMono: Long,
            allowed: Set<Pair<String, String>>) {
    require(enabled && epoch >= leaseEpoch && revision >= grantRevision) { "stale_lease" }
    require(epoch > leaseEpoch || (leaseUntil > nowMono && leaseUntil != 0L)) { "stale_lease" }
    require(expiresUnix > serverUnix && expiresUnix - serverUnix <= 30_000) { "invalid_lease" }
    val trustedNow = maxOf(serverUnix, if (anchorUnix > 0) anchorUnix + (nowMono - anchorMono) else serverUnix)
    require(expiresUnix > trustedNow) { "expired_lease" }
    leaseEpoch = epoch; grantRevision = revision; anchorUnix = trustedNow; anchorMono = nowMono
    leaseUntil = nowMono + (expiresUnix - trustedNow); grants = allowed
  }

  fun check(c: CommandIdentity, nowMono: Long, wallUnixMs: Long? = null): String? {
    if (!enabled || controlSessionId.isEmpty()) return "locally_stopped"
    if (c.authorityId != authorityId || c.deviceId != deviceId) return "wrong_authority_or_device"
    if (c.bootId != bootId || c.controlSessionId != controlSessionId) return "stale_control_session"
    if (c.leaseEpoch != leaseEpoch || nowMono >= leaseUntil) return "stale_lease"
    if (c.grantRevision != grantRevision || c.capabilityRevision != 1L) return "stale_revision"
    if (c.resourceId !in localApps || (c.capability to c.resourceId) !in grants) return "not_authorized"
    val nowUnix = maxOf(anchorUnix + (nowMono - anchorMono), wallUnixMs ?: 0)
    if (c.ttlMs !in 1..30_000 || c.issuedUnixMs > nowUnix + 1_000 || c.deadlineUnixMs <= c.issuedUnixMs ||
      nowUnix >= minOf(c.deadlineUnixMs, c.issuedUnixMs + c.ttlMs)) return "expired"
    return null
  }

  fun remaining(c: CommandIdentity, nowMono: Long, wallUnixMs: Long? = null): Long =
    minOf(leaseUntil - nowMono, c.ttlMs,
      minOf(c.deadlineUnixMs, c.issuedUnixMs + c.ttlMs) - maxOf(anchorUnix + nowMono - anchorMono, wallUnixMs ?: 0))
}
