// Draws the logo (frontend/src/shell/favicon.svg) into an .iconset at every size macOS asks for, on Apple's icon grid: the
// artwork fills 824 of 1024 points, centred, so that AXIOM sits at the same size as its neighbours in the Dock.
//
//   make_icon favicon.svg AppIcon.iconset && iconutil -c icns AppIcon.iconset

#import <Cocoa/Cocoa.h>

static void draw(NSImage *logo, int pixels, NSString *path) {
  NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL
                                                                  pixelsWide:pixels
                                                                  pixelsHigh:pixels
                                                               bitsPerSample:8
                                                             samplesPerPixel:4
                                                                    hasAlpha:YES
                                                                    isPlanar:NO
                                                              colorSpaceName:NSDeviceRGBColorSpace
                                                                 bytesPerRow:0
                                                                bitsPerPixel:0];
  [NSGraphicsContext saveGraphicsState];
  NSGraphicsContext.currentContext = [NSGraphicsContext graphicsContextWithBitmapImageRep:rep];
  NSGraphicsContext.currentContext.imageInterpolation = NSImageInterpolationHigh;
  double side = pixels * 824.0 / 1024.0, inset = (pixels - side) / 2;
  [logo drawInRect:NSMakeRect(inset, inset, side, side)
          fromRect:NSZeroRect
         operation:NSCompositingOperationSourceOver
          fraction:1.0];
  [NSGraphicsContext restoreGraphicsState];
  [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
}

int main(int argc, const char **argv) {
  @autoreleasepool {
    if (argc != 3) {
      fprintf(stderr, "usage: make_icon logo.svg out.iconset\n");
      return 2;
    }
    NSImage *logo = [[NSImage alloc] initWithContentsOfFile:@(argv[1])];
    if (!logo.isValid) {
      fprintf(stderr, "make_icon: cannot read %s\n", argv[1]);
      return 1;
    }
    NSString *out = @(argv[2]);
    [NSFileManager.defaultManager createDirectoryAtPath:out withIntermediateDirectories:YES attributes:nil error:nil];
    for (NSNumber *size in @[ @16, @32, @128, @256, @512 ]) {
      int points = size.intValue;
      draw(logo, points, [out stringByAppendingFormat:@"/icon_%dx%d.png", points, points]);
      draw(logo, points * 2, [out stringByAppendingFormat:@"/icon_%dx%d@2x.png", points, points]);
    }
  }
  return 0;
}
