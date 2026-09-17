package expo.modules.v8executor

import org.junit.Assert.*
import org.junit.Test

class JpegMetadataTest {
  private fun bytes(vararg values: Int) = values.map { it.toByte() }.toByteArray()
  @Test fun removesAppMetadataWithoutChangingEntropyCodedPixels() {
    val start = bytes(0xff, 0xd8, 0xff, 0xe0, 0, 4, 1, 2)
    val scan = bytes(0xff, 0xda, 0, 4, 3, 4, 0x12, 0xff, 0, 0xe2, 0x34, 0xff, 0xd9)
    val icc = bytes(0xff, 0xe2, 0, 6, 73, 67, 67, 0)
    val exif = bytes(0xff, 0xe1, 0, 5, 1, 2, 3)
    val comment = bytes(0xff, 0xfe, 0, 4, 8, 9)
    assertArrayEquals(start + scan, JpegMetadata.strip(start + icc + exif + comment + scan))
    assertArrayEquals(start + scan, JpegMetadata.strip(start + scan))
  }
  @Test fun truncatedOrMalformedEncoderSegmentsCannotBecomeAnImage() {
    for (sample in listOf(bytes(0xff, 0xd8, 0xff, 0xd9), bytes(0xff, 0xd8, 0xff, 0xe2, 0, 20, 0xff, 0xd9),
        bytes(0xff, 0xd8, 0xff, 0xe2, 0, 1, 0xff, 0xd9))) {
      assertThrows(IllegalArgumentException::class.java) { JpegMetadata.strip(sample) }
    }
  }
}
