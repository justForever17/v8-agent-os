package expo.modules.v8executor

import org.junit.Assert.*
import org.junit.Test

class CommandGuardTest {
  private fun armed(): CommandGuard = CommandGuard("A", "phone", "boot", 7).apply {
    localApps = setOf("test.fixture")
    arm("local-session")
    lease(8, 2, 1_000_000, 1_030_000, 100, setOf("android.action" to "test.fixture", "android.observe" to "test.fixture"))
  }
  private fun command() = CommandIdentity("A", "phone", "boot", "local-session", 8, 2, 1,
    1_000_000, 1_020_000, 10_000, "android.action", "test.fixture")

  @Test fun validActionIsAdmittedOnlyInsideBothScopes() {
    val guard = armed()
    assertNull(guard.check(command(), 101))
    assertEquals("not_authorized", guard.check(command().copy(resourceId = "other.app"), 101))
    guard.localApps = emptySet()
    assertEquals("not_authorized", guard.check(command(), 101))
  }
  @Test fun stopCannotBeRearmedByNetworkRenewalOrOldCommand() {
    val guard = armed(); guard.stop()
    assertEquals("locally_stopped", guard.check(command(), 102))
    assertThrows(IllegalArgumentException::class.java) { guard.lease(9, 2, 1_000_000, 1_030_000, 100, emptySet()) }
    guard.arm("new-local-session")
    guard.lease(9, 2, 1_000_000, 1_030_000, 100, setOf("android.action" to "test.fixture"))
    assertEquals("stale_control_session", guard.check(command(), 103))
  }
  @Test fun anotherAuthorityWithEqualEpochCannotTakeControl() {
    assertEquals("wrong_authority_or_device", armed().check(command().copy(authorityId = "B"), 101))
    assertEquals("wrong_authority_or_device", armed().check(command().copy(deviceId = "other-phone"), 101))
  }
  @Test fun rebootEpochAndGrantChangesFenceOldCommands() {
    val guard = armed()
    assertEquals("stale_control_session", guard.check(command().copy(bootId = "previous-boot"), 101))
    assertEquals("stale_lease", guard.check(command().copy(leaseEpoch = 7), 101))
    assertEquals("stale_revision", guard.check(command().copy(grantRevision = 1), 101))
    assertEquals("stale_revision", guard.check(command().copy(capabilityRevision = 0), 101))
    assertThrows(IllegalArgumentException::class.java) { guard.lease(7, 2, 1_000_000, 1_030_000, 101, emptySet()) }
  }
  @Test fun deadlineAndOriginalTtlDoNotResetOnReconnect() {
    val guard = armed()
    assertEquals("expired", guard.check(command(), 10_100))
    assertEquals("expired", guard.check(command().copy(deadlineUnixMs = 1_000_001), 102))
    guard.disconnect()
    assertEquals("stale_lease", guard.check(command(), 102))
    guard.lease(9, 2, 1_011_000, 1_041_000, 11_100, setOf("android.action" to "test.fixture"))
    assertEquals("expired", guard.check(command().copy(leaseEpoch = 9), 11_100))
  }
  @Test fun monotonicTimeAndLeaseBoundDriverBudget() {
    val guard = armed()
    assertEquals(9_999, guard.remaining(command(), 101))
    assertEquals("stale_lease", guard.check(command(), 30_100))
    assertEquals("expired", guard.check(command().copy(issuedUnixMs = 1_005_000), 101))
    assertEquals("expired", guard.check(command().copy(ttlMs = 60_000), 101))
    assertEquals("expired", guard.check(command(), 101, 1_050_000))
    assertTrue(guard.remaining(command(), 101, 1_050_000) < 0)
  }
  @Test fun expiredDisconnectedAndRearmedSeatsRequireANewEpoch() {
    val scopes = setOf("android.action" to "test.fixture")
    assertThrows(IllegalArgumentException::class.java) { armed().lease(8, 2, 1_030_000, 1_060_000, 30_100, scopes) }
    val disconnected = armed(); disconnected.disconnect()
    assertThrows(IllegalArgumentException::class.java) { disconnected.lease(8, 2, 1_000_001, 1_030_001, 101, scopes) }
    val rearmed = armed(); rearmed.arm("new-session")
    assertThrows(IllegalArgumentException::class.java) { rearmed.lease(8, 2, 1_000_001, 1_030_001, 101, scopes) }
    disconnected.lease(9, 2, 1_000_001, 1_030_001, 101, scopes)
    assertNull(disconnected.check(command().copy(leaseEpoch = 9), 102))
  }
  @Test fun backwardServerTimeCannotRestoreCommandTtl() {
    val guard = armed()
    guard.lease(8, 2, 999_000, 1_029_000, 1_100, setOf("android.action" to "test.fixture"))
    assertEquals(9_000, guard.remaining(command(), 1_100))
    assertEquals("expired", guard.check(command(), 10_100))
    assertThrows(IllegalArgumentException::class.java) { guard.lease(8, 2, 900_000, 930_000, 1_101, emptySet()) }
    assertThrows(IllegalArgumentException::class.java) { guard.lease(9, 2, 1_001_000, 1_061_000, 1_101, emptySet()) }
  }
}
