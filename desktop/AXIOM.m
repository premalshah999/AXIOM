// AXIOM's window: one WKWebView, the Mac's menus, and a safe Quit. Everything else is jobpilot/native (docs/DESKTOP.md).
//
// It starts `python -m jobpilot.native.desktop boot`, which becomes the supervisor, then watches the two small files that
// process writes: boot.json until it is running, state.json after. There is no socket or API between the two, so the
// window can never be what an application waits on, and if the window dies the supervisor notices and drains (supervisor.py,
// tick_owner).
//
// Objective-C rather than Swift only because Swift cannot import Cocoa on Command Line Tools 16.2 ("redefinition of module
// SwiftBridging", a packaging bug in the toolchain). The binary is about 150 KB either way.

#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

typedef NS_ENUM(NSInteger, Mode) { ModeSplash, ModeDashboard, ModeFailed, ModeQuitting };

static NSString *Home(void) {
  // HOME first, as desktop.py does, so that a test instance stays in its scratch folder. Finder always sets it.
  const char *home = getenv("HOME");
  return home && *home ? @(home) : NSHomeDirectory();
}
static NSString *Support(void) { return [Home() stringByAppendingPathComponent:@"Library/Application Support/AXIOM"]; }
static NSString *Data(void) { return [Support() stringByAppendingPathComponent:@"data"]; }
static NSString *Native(NSString *name) {
  return [[Data() stringByAppendingPathComponent:@"native"] stringByAppendingPathComponent:name];
}
static NSString *Logs(void) { return [Home() stringByAppendingPathComponent:@"Library/Logs/AXIOM"]; }

static void Log(NSString *line) {
  static NSDateFormatter *stamp;
  if (!stamp) {
    stamp = [NSDateFormatter new];
    stamp.dateFormat = @"yyyy-MM-dd HH:mm:ss ";
  }
  NSString *path = [Logs() stringByAppendingPathComponent:@"window.log"];
  [NSFileManager.defaultManager createDirectoryAtPath:Logs() withIntermediateDirectories:YES attributes:nil error:nil];
  if (![NSFileManager.defaultManager fileExistsAtPath:path])
    [NSFileManager.defaultManager createFileAtPath:path contents:nil attributes:@{NSFilePosixPermissions : @0600}];
  NSFileHandle *file = [NSFileHandle fileHandleForWritingAtPath:path];
  [file seekToEndOfFile];
  [file writeData:[[[[stamp stringFromDate:NSDate.date] stringByAppendingString:line] stringByAppendingString:@"\n"]
                      dataUsingEncoding:NSUTF8StringEncoding]];
  [file closeFile];
}

static NSDictionary *ReadJSON(NSString *path) {
  NSData *data = [NSData dataWithContentsOfFile:path];
  id parsed = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
  return [parsed isKindOfClass:NSDictionary.class] ? parsed : @{};
}

static NSInteger IntOr(id value, NSInteger fallback) { return [value isKindOfClass:NSNumber.class] ? [value integerValue] : fallback; }

static NSString *JSString(NSString *text) {
  NSData *data = [NSJSONSerialization dataWithJSONObject:@[ text ?: @"" ] options:0 error:nil];
  NSString *array = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
  return [array substringWithRange:NSMakeRange(1, array.length - 2)];
}

// The face of the app while nothing else can answer. Its colours are the logo's.
static NSString *const SplashHTML =
    @"<!doctype html><meta charset=utf-8><title>AXIOM</title><style>"
     ":root{--bg:#f4f1ea;--fg:#100f0d;--dim:#6b675e;--line:#dcd7ca}"
     "@media(prefers-color-scheme:dark){:root{--bg:#100f0d;--fg:#ede9dd;--dim:#97928a;--line:#2b2925}}"
     "html,body{height:100%;margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui;cursor:default;"
     "-webkit-user-select:none}body{display:flex;align-items:center;justify-content:center}"
     "main{width:min(440px,84vw)}svg{width:44px;height:44px;display:block;margin-bottom:22px}"
     "h1{font-size:20px;font-weight:600;margin:0 0 6px}p{margin:0;color:var(--dim);min-height:46px;-webkit-user-select:text}"
     "#bar{height:3px;background:var(--line);margin-top:18px;border-radius:2px;overflow:hidden;visibility:hidden}"
     "#fill{height:100%;width:0;background:#ff4310;transition:width .3s}"
     "#acts{margin-top:18px;display:none;gap:18px;flex-wrap:wrap}a{color:#ff4310;text-decoration:none}a:hover{text-decoration:underline}"
     "</style><main><svg viewBox='0 0 32 32'><circle cx=16 cy=16 r=10.5 fill=none stroke=currentColor stroke-width=2.4 />"
     "<circle cx=16 cy=16 r=4.4 fill=#ff4310 /></svg><h1 id=t>Starting AXIOM</h1><p id=m></p><div id=bar><div id=fill></div></div>"
     "<div id=acts></div></main>"
     // `kind` picks the links: a failure offers a retry, a plain stop only offers to open AXIOM again, and a copy that stopped
     // part way can only be fixed by copying it again.
     "<script>var K={fail:[['Try again','retry'],['Copy diagnostics','diagnostics'],['Open logs','logs']],"
     "stopped:[['Open AXIOM again','retry']],damaged:[['Show in Finder','reveal'],['Copy diagnostics','diagnostics']]};"
     "function show(t,m,p,f){document.getElementById('t').textContent=t;document.getElementById('m').textContent=m;"
     "var b=document.getElementById('bar');b.style.visibility=p==null?'hidden':'visible';"
     "if(p!=null)document.getElementById('fill').style.width=p+'%';"
     "var a=document.getElementById('acts');a.textContent='';(K[f]||[]).forEach(function(x){var e=document.createElement('a');"
     "e.textContent=x[0];e.href='axiom://'+x[1];a.appendChild(e)});a.style.display=K[f]?'flex':'none'}</script>";

// The dashboard says when its session ended (frontend/src/api.ts, SIGNED_OUT). On your own Mac that is not a reason to show a
// sign-in screen, so the window signs in again. The API's token check stays exactly as it is.
static NSString *const SignedOutScript =
    @"window.addEventListener('applypilot:signed-out',function(){window.webkit.messageHandlers.axiom.postMessage('signed-out')});";

// What a drag from Finder hands the view, for the driver below.
@interface DriveDrag : NSObject <NSDraggingInfo>
@property(strong) NSPasteboard *board;
@property(strong) NSWindow *window;
@property(assign) NSPoint at;
@property NSDraggingFormation draggingFormation;
@property BOOL animatesToDestination;
@property NSInteger numberOfValidItemsForDrop;
@property(readonly) NSSpringLoadingHighlight springLoadingHighlight;
@end

@implementation DriveDrag
- (NSWindow *)draggingDestinationWindow { return self.window; }
- (NSDragOperation)draggingSourceOperationMask { return NSDragOperationEvery; }
- (NSPoint)draggingLocation { return self.at; }
- (NSPoint)draggedImageLocation { return self.at; }
- (NSImage *)draggedImage { return nil; }
- (NSPasteboard *)draggingPasteboard { return self.board; }
- (id)draggingSource { return nil; }
- (NSInteger)draggingSequenceNumber { return 1; }
- (void)slideDraggedImageTo:(NSPoint)point {}
- (NSArray<NSString *> *)namesOfPromisedFilesDroppedAtDestination:(NSURL *)destination { return nil; }
- (void)enumerateDraggingItemsWithOptions:(NSDraggingItemEnumerationOptions)options forView:(NSView *)view classes:(NSArray<Class> *)classes searchOptions:(NSDictionary *)search usingBlock:(void (^)(NSDraggingItem *, NSInteger, BOOL *))block {}
- (NSSpringLoadingHighlight)springLoadingHighlight { return NSSpringLoadingHighlightNone; }
- (void)resetSpringLoading {}
@end

@interface AppDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate, WKNavigationDelegate, WKUIDelegate,
                                   WKDownloadDelegate, WKScriptMessageHandler>
@property(strong) NSWindow *window;
@property(strong) WKWebView *web;
@property(strong) NSStatusItem *statusItem;
@property(strong) NSAlert *batteryAlert;
@property(strong) NSMenuItem *loginItem;
@property(strong) NSTask *boot;
@property(strong) NSTimer *timer;
@property(strong) NSDictionary *state;
@property(strong) NSDictionary *info;
@property(strong) NSString *base;
@property(strong) NSMutableArray<NSWindow *> *extraWindows;
@property(strong) NSMapTable<WKDownload *, NSURL *> *downloads;
@property(strong) NSDate *launched;
@property(strong) NSDate *quitDeadline;
@property(strong) NSDate *bootEnded;
@property(strong) NSDate *lastSignIn;
@property(strong) NSString *splashKey;
@property(assign) Mode mode;
@property(assign) BOOL splashLoaded;
@property(assign) BOOL triedBoot;
@property(assign) BOOL signingIn;
@property(assign) BOOL dashboardTimed;
@property(assign) BOOL quietQuit;
@property(assign) BOOL batteryWarned;
@property(assign) BOOL damaged;
@property(assign) BOOL bootSignalled;
@property(strong) NSAlert *updateAlert;
@property(strong) NSString *updateURL;
@property(assign) int bootStatus;
@property(assign) pid_t verifiedPid;
@property(assign) NSInteger snapshots;
@property(strong) NSString *driveFile;
@property(strong) NSString *driveSaveDir;
@property(strong) NSTimer *driveTimer;
@end

@implementation AppDelegate

// ---- launch ------------------------------------------------------------------------------------------------------------

- (void)applicationDidFinishLaunching:(NSNotification *)note {
  self.launched = NSDate.date;
  self.extraWindows = [NSMutableArray new];
  self.downloads = [NSMapTable weakToStrongObjectsMapTable];
  Log([NSString stringWithFormat:@"Opened from %@ (pid %d).", NSBundle.mainBundle.bundlePath, getpid()]);
  [self buildMenus];
  if ([self refuseWhereItIs]) return;
  [self buildWindow];
  [self buildStatusItem];
  if (getenv("AXIOM_SHELL_DRIVE")) {
    self.driveTimer = [NSTimer timerWithTimeInterval:0.2 target:self selector:@selector(driveTick) userInfo:nil repeats:YES];
    [NSRunLoop.mainRunLoop addTimer:self.driveTimer forMode:NSRunLoopCommonModes];
  }
  NSString *incomplete = [self installProblem];
  if (incomplete) {
    // Python and PostgreSQL are in this bundle, so a copy that stopped part way fails with a Python error that means nothing to
    // anyone ("No module named importlib", 2026-11-01). Say what happened, and what to do, before starting anything.
    Log([@"The installed copy is incomplete: " stringByAppendingString:incomplete]);
    self.damaged = YES;
    self.mode = ModeFailed;
    [self showSplash:@"AXIOM was not copied completely"
             message:@"Some of AXIOM's files did not make it to this Mac, so it cannot start. Move AXIOM to the Bin, then drag it from the "
                     @"AXIOM disk image to Applications again, using Finder. If the Mac says \"Operation not permitted\", copy it with "
                     @"Finder rather than Terminal."
             percent:nil
                kind:@"damaged"];
    return;  // No supervisor, no timers: Quit has nothing to wait for.
  }

  // `kill` is how a test or a script asks for Quit; it takes the same draining path as the menu.
  signal(SIGTERM, SIG_IGN);
  static dispatch_source_t term;
  term = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0, dispatch_get_main_queue());
  dispatch_source_set_event_handler(term, ^{
    self.quietQuit = YES;
    [self dismissPanels];
    [NSApp terminate:nil];
  });
  dispatch_resume(term);
  [NSWorkspace.sharedWorkspace.notificationCenter addObserverForName:NSWorkspaceWillPowerOffNotification
                                                              object:nil
                                                               queue:nil
                                                          usingBlock:^(NSNotification *n) {
                                                            self.quietQuit = YES;
                                                            [self dismissPanels];
                                                          }];
  [self tick];
  // The common modes: while an NSTerminateLater quit waits, the run loop is in a modal mode, and a default-mode timer never
  // fires. Quit hung forever without this, on its first test.
  self.timer = [NSTimer timerWithTimeInterval:1.0 target:self selector:@selector(tick) userInfo:nil repeats:YES];
  self.timer.tolerance = 0.4;
  [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
}

// AppKit ignores Quit while a sheet or modal panel is up. Nobody is there to answer one when the Mac is shutting down.
- (void)dismissPanels {
  if (self.window.attachedSheet) [self.window endSheet:self.window.attachedSheet];
  if (NSApp.modalWindow) [NSApp abortModal];
}

// Run from the disk image or a quarantined Downloads folder, macOS gives the app a read-only random path that disappears
// when the image is ejected, and the database would lose its programs mid-write.
- (BOOL)refuseWhereItIs {
  NSString *path = NSBundle.mainBundle.bundlePath;
  if (![path containsString:@"/AppTranslocation/"] && ![path hasPrefix:@"/Volumes/"]) return NO;
  NSAlert *alert = [NSAlert new];
  alert.messageText = @"Move AXIOM to Applications first";
  alert.informativeText = @"AXIOM is running from the disk image or a temporary folder. Drag it to your Applications folder, "
                          @"then open it from there.";
  [alert addButtonWithTitle:@"Quit"];
  [NSApp activateIgnoringOtherApps:YES];
  [alert runModal];
  [NSApp terminate:nil];
  return YES;
}

// What a whole copy has: the files the app cannot start without, and every file of the build at its size (install-manifest.txt,
// written by desktop/build.sh just before signing). Returns nil when all is well, else a sentence for the log.
- (NSString *)installProblem {
  NSString *resources = NSBundle.mainBundle.resourcePath;
  for (NSString *relative in @[ @"python/bin/python3", @"python/lib/python3.12/importlib/__init__.py", @"app/jobpilot/native/desktop.py",
                                @"postgres/bin/postgres", @"tex/bin/universal-darwin/pdflatex" ]) {
    if (access([resources stringByAppendingPathComponent:relative].fileSystemRepresentation, R_OK) != 0)
      return [relative stringByAppendingString:@" is missing"];
  }
  NSString *manifest = [NSString stringWithContentsOfFile:[resources stringByAppendingPathComponent:@"install-manifest.txt"]
                                                 encoding:NSUTF8StringEncoding
                                                    error:nil];
  if (!manifest) return nil;  // A build from before the manifest, or a test bundle: the key files above were enough.
  NSInteger bad = 0, total = 0;
  NSString *first = nil;
  for (NSString *line in [manifest componentsSeparatedByString:@"\n"]) {
    NSRange tab = [line rangeOfString:@"\t"];
    if (!tab.length) continue;
    total++;
    NSString *relative = [line substringFromIndex:NSMaxRange(tab)];
    struct stat info;
    if (lstat([resources stringByAppendingPathComponent:relative].fileSystemRepresentation, &info) != 0 ||
        (long long)info.st_size != [[line substringToIndex:tab.location] longLongValue]) {
      if (!first) first = relative;
      bad++;
    }
  }
  return bad ? [NSString stringWithFormat:@"%ld of %ld files are missing or damaged, such as %@", (long)bad, (long)total, first] : nil;
}

- (void)buildWindow {
  WKWebViewConfiguration *config = [WKWebViewConfiguration new];
  // The default store persists: the theme and the guide's "later" live in localStorage, under the app's sticky port.
  config.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
  // "Open the site" runs after an await, by which time WebKit no longer counts it as a click.
  config.preferences.javaScriptCanOpenWindowsAutomatically = YES;
  WKUserScript *script = [[WKUserScript alloc] initWithSource:SignedOutScript
                                                injectionTime:WKUserScriptInjectionTimeAtDocumentStart
                                             forMainFrameOnly:YES];
  [config.userContentController addUserScript:script];
  [config.userContentController addScriptMessageHandler:self name:@"axiom"];
  self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1320, 860)
                                            styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                                      NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
                                              backing:NSBackingStoreBuffered
                                                defer:NO];
  self.window.title = @"AXIOM";
  self.window.minSize = NSMakeSize(900, 600);
  self.window.delegate = self;
  self.window.releasedWhenClosed = NO;
  [self.window center];
  [self.window setFrameAutosaveName:@"AXIOM"];
  self.web = [self webViewWithConfiguration:config];
  self.window.contentView = self.web;
  if (getenv("AXIOM_SHELL_BACKGROUND")) {
    [self.window orderBack:nil];  // A test instance beside someone working must not take the keyboard.
  } else {
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
  }
}

- (WKWebView *)webViewWithConfiguration:(WKWebViewConfiguration *)config {
  WKWebView *web = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, 1320, 860) configuration:config];
  web.navigationDelegate = self;
  web.UIDelegate = self;
  web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
  web.allowsBackForwardNavigationGestures = YES;
  return web;
}

// ---- the watch ---------------------------------------------------------------------------------------------------------

- (BOOL)supervisorAlive:(NSDictionary *)state {
  pid_t pid = (pid_t)IntOr(state[@"pid"], 0);
  if (pid <= 0 || kill(pid, 0) != 0) return NO;
  if (pid == self.verifiedPid) return YES;
  // state.json outlives its process, and a pid gets reused: the same check as supervisor.same_process.
  NSTask *ps = [NSTask new];
  ps.executableURL = [NSURL fileURLWithPath:@"/bin/ps"];
  ps.arguments = @[ @"-o", @"lstart=", @"-p", [NSString stringWithFormat:@"%d", pid] ];
  ps.environment = @{@"LC_ALL" : @"C", @"TZ" : @"UTC"};
  NSPipe *out = [NSPipe pipe];
  ps.standardOutput = out;
  ps.standardError = NSFileHandle.fileHandleWithNullDevice;
  if (![ps launchAndReturnError:nil]) return NO;
  NSData *data = [out.fileHandleForReading readDataToEndOfFile];
  [ps waitUntilExit];
  NSString *started = [[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]
      stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
  if (![started isEqual:state[@"lstart"]]) return NO;
  self.verifiedPid = pid;
  return YES;
}

- (void)tick {
  if (self.damaged) return;  // An incomplete copy has nothing to start: the screen says so, and Quit still works.
  NSDictionary *state = ReadJSON(Native(@"state.json"));
  self.state = state;
  BOOL alive = [self supervisorAlive:state];
  [self watchPower:state alive:alive];
  if (self.mode == ModeQuitting) {
    NSInteger active = IntOr(state[@"active"], 0);
    if (!alive || [NSDate.date compare:self.quitDeadline] == NSOrderedDescending) {
      // Past the deadline the supervisor is still finishing, and will: it does not need this window to do it.
      Log(alive ? @"Quit: closing the window while the supervisor finishes." : @"Quit: everything has stopped.");
      [NSApp replyToApplicationShouldTerminate:YES];
    } else {
      [self showSplash:@"Finishing up"
               message:active > 0 ? [NSString stringWithFormat:@"Finishing %ld application%@ safely. This can take up to three minutes, "
                                                               @"and AXIOM closes by itself.",
                                                               (long)active, active == 1 ? @"" : @"s"]
                                  : @"Saving your work. AXIOM closes by itself."
               percent:nil
                kind:nil];
    }
    return;
  }
  NSString *phase = state[@"phase"];
  if (alive && [phase isEqual:@"running"]) {
    if (self.mode != ModeDashboard) [self showDashboard];
    return;
  }
  if (alive) {
    // A window opened again while the last one's supervisor is still draining waits for it, then starts.
    self.mode = ModeSplash;
    [phase isEqual:@"stopping"] ? [self showSplash:@"Finishing the last session" message:@"Saving your work first. This can take up to three minutes." percent:nil kind:nil]
                                : [self showSplash:@"Starting AXIOM" message:[self startingLine:state] percent:nil kind:nil];
    return;
  }
  if (self.boot.running) {
    self.mode = ModeSplash;
    NSDictionary *progress = ReadJSON(Native(@"boot.json"));
    [self showSplash:@"Starting AXIOM" message:progress[@"message"] ?: @"" percent:progress[@"percent"] kind:nil];
    return;
  }
  if (!self.triedBoot) {
    [self startBoot];
    return;
  }
  if (self.mode == ModeFailed) return;
  // Exit 0 without a supervisor: another copy holds the boot lock, and its state.json is on its way.
  // Not when the supervisor has said it stopped: that is its own word, and twenty seconds of "finishing" afterwards reads as stuck.
  if (self.bootStatus == 0 && self.bootEnded && -self.bootEnded.timeIntervalSinceNow < 20 && self.mode != ModeDashboard &&
      ![state[@"phase"] isEqual:@"stopped"])
    return;
  [self showFailure:state];
}

- (NSString *)startingLine:(NSDictionary *)state {
  NSDictionary *children = state[@"children"];
  if (![children isKindOfClass:NSDictionary.class]) return @"";
  for (NSString *name in @[ @"postgres", @"worker", @"twin", @"app" ]) {
    NSString *status = children[name][@"state"];
    if (status && ![status isEqual:@"up"] && ![status isEqual:@"external"]) {
      if ([name isEqual:@"postgres"]) return @"Starting the database ...";
      if ([name isEqual:@"app"]) return @"Almost there ...";
      return @"Starting Chrome ...";
    }
  }
  return @"";
}

// A person who sees "could not start" after an ordinary stop (a Quit from the Terminal, Activity Monitor, a restart from Settings
// that took the supervisor with it) believes AXIOM is broken. The supervisor says how it ended: phase "stopped", with a reason only
// when it failed. Anything else that is gone with no word is a signal from outside, or a crash.
- (void)showFailure:(NSDictionary *)state {
  self.mode = ModeFailed;
  self.base = nil;
  NSDictionary *progress = ReadJSON(Native(@"boot.json"));
  NSString *reason = [progress[@"step"] isEqual:@"failed"] ? progress[@"message"] : state[@"reason"];
  BOOL hasReason = [reason isKindOfClass:NSString.class] && reason.length;
  BOOL plainStop = [state[@"phase"] isEqual:@"stopped"] &&
                   (!hasReason || [reason isEqual:@"Stopped by request."] || [reason hasPrefix:@"The AXIOM window went away"]);
  BOOL outside = !hasReason && [state[@"phase"] isEqual:@"running"] && self.bootSignalled;
  if (plainStop || outside) {
    Log(plainStop ? @"AXIOM was stopped; showing the stopped screen." : @"AXIOM was stopped from outside; showing the stopped screen.");
    [self showSplash:@"AXIOM has stopped"
             message:outside ? @"Something else on this Mac closed AXIOM. Your work is safe. Open it again to carry on."
                             : @"AXIOM was stopped, and nothing was lost. Open it again to carry on."
             percent:nil
                kind:@"stopped"];
    return;
  }
  if (!hasReason) reason = @"AXIOM stopped unexpectedly. Your work is safe. Try again, and if it happens again use Copy diagnostics and send it to the person who gave you AXIOM.";
  Log([@"Showing a failure: " stringByAppendingString:reason]);
  [self showSplash:[progress[@"step"] isEqual:@"failed"] ? @"AXIOM could not start" : @"AXIOM stopped unexpectedly"
           message:reason
           percent:nil
              kind:@"fail"];
  [self.window makeKeyAndOrderFront:nil];
}

- (void)startBoot {
  self.triedBoot = YES;
  self.bootEnded = nil;
  self.mode = ModeSplash;
  self.splashKey = nil;
  [self showSplash:@"Starting AXIOM" message:@"" percent:nil kind:nil];
  NSString *resources = NSBundle.mainBundle.resourcePath;
  NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
  // The app's Python and nothing else's. A terminal can hand down any of these.
  [env removeObjectsForKeys:@[ @"PYTHONHOME", @"VIRTUAL_ENV", @"CONDA_PREFIX", @"UV_PROJECT_ENVIRONMENT" ]];
  env[@"PYTHONPATH"] = [resources stringByAppendingPathComponent:@"app"];
  env[@"PYTHONDONTWRITEBYTECODE"] = @"1";
  env[@"PYTHONNOUSERSITE"] = @"1";
  // Otherwise the working directory comes first on sys.path. Started from a checkout's folder, this imported that
  // checkout's `jobpilot` instead of its own (2026-10-31, on the first launch beside production).
  env[@"PYTHONSAFEPATH"] = @"1";
  env[@"AXIOM_BUNDLE"] = resources;
  env[@"OPENSSL_CONF"] = [resources stringByAppendingPathComponent:@"openssl.cnf"];
  NSTask *task = [NSTask new];
  task.executableURL = [NSURL fileURLWithPath:[resources stringByAppendingPathComponent:@"python/bin/python3"]];
  task.arguments = @[ @"-u", @"-m", @"jobpilot.native.desktop", @"boot", [NSString stringWithFormat:@"%d", getpid()] ];
  task.environment = env;
  task.currentDirectoryURL = [NSURL fileURLWithPath:resources];
  task.standardInput = NSFileHandle.fileHandleWithNullDevice;
  // What fails before Python can report it (an import, a damaged bundle) has to reach the log the failure screen points at.
  NSString *logPath = [Logs() stringByAppendingPathComponent:@"supervisor.log"];
  [NSFileManager.defaultManager createDirectoryAtPath:Logs() withIntermediateDirectories:YES attributes:nil error:nil];
  if (![NSFileManager.defaultManager fileExistsAtPath:logPath])
    [NSFileManager.defaultManager createFileAtPath:logPath contents:nil attributes:@{NSFilePosixPermissions : @0600}];
  NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:logPath];
  [log seekToEndOfFile];
  task.standardOutput = log ?: NSFileHandle.fileHandleWithNullDevice;
  task.standardError = log ?: NSFileHandle.fileHandleWithNullDevice;
  __weak AppDelegate *weakSelf = self;
  task.terminationHandler = ^(NSTask *ended) {
    dispatch_async(dispatch_get_main_queue(), ^{
      weakSelf.bootStatus = ended.terminationStatus;
      weakSelf.bootEnded = NSDate.date;
      // A signal's number is not an exit status: 15 is the Mac's polite "please stop", which Activity Monitor, `axiom stop` and a
      // shutdown all send.
      Log(ended.terminationReason == NSTaskTerminationReasonUncaughtSignal
              ? [NSString stringWithFormat:@"The supervisor was stopped by signal %d.", ended.terminationStatus]
              : [NSString stringWithFormat:@"The supervisor exited with status %d.", ended.terminationStatus]);
      weakSelf.bootSignalled = ended.terminationReason == NSTaskTerminationReasonUncaughtSignal;
    });
  };
  NSError *error;
  if (![task launchAndReturnError:&error]) {
    Log([@"Could not start: " stringByAppendingString:error.localizedDescription]);
    self.bootStatus = 1;
    self.bootEnded = NSDate.date;
    return;
  }
  self.boot = task;
  Log([NSString stringWithFormat:@"Started the supervisor (pid %d).", task.processIdentifier]);
}

- (void)showSplash:(NSString *)title message:(NSString *)message percent:(id)percent kind:(NSString *)kind {
  NSString *number = [percent isKindOfClass:NSNumber.class] ? [percent stringValue] : @"null";
  NSString *call = [NSString stringWithFormat:@"show(%@,%@,%@,%@)", JSString(title), JSString(message), number, JSString(kind)];
  if ([call isEqual:self.splashKey] && self.splashLoaded) return;
  self.splashKey = call;
  if (self.splashLoaded) {
    [self.web evaluateJavaScript:call completionHandler:nil];
  } else if (!self.web.isLoading || self.base) {
    self.base = nil;
    [self.web loadHTMLString:SplashHTML baseURL:nil];
  }
}

// ---- the dashboard -----------------------------------------------------------------------------------------------------

- (void)showDashboard {
  if (self.signingIn) return;
  self.info = ReadJSON(Native(@"window.json"));
  NSString *base = self.info[@"base_url"];
  if (![base isKindOfClass:NSString.class]) {
    [self showFailure:self.state];
    return;
  }
  self.base = base;
  [self signInThen:^{
    self.mode = ModeDashboard;
    self.splashLoaded = NO;
    // Never a stored page: it names the assets of an older build, so an install would not show until a reload.
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:base]
                                           cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                       timeoutInterval:60]];
  }];
}

// A session cookie from the API's own sign-in, placed in the web view's store. The token never reaches the page.
- (void)signInThen:(void (^)(void))then {
  self.signingIn = YES;
  self.lastSignIn = NSDate.date;
  NSURLSessionConfiguration *config = NSURLSessionConfiguration.ephemeralSessionConfiguration;
  config.connectionProxyDictionary = @{};  // Loopback must never go through a proxy someone has configured.
  config.HTTPCookieAcceptPolicy = NSHTTPCookieAcceptPolicyNever;
  NSURL *url = [NSURL URLWithString:[self.base stringByAppendingString:@"/api/login"]];
  NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
  request.HTTPMethod = @"POST";
  [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
  request.HTTPBody = [NSJSONSerialization dataWithJSONObject:@{@"token" : self.info[@"token"] ?: @"", @"keep" : @YES} options:0 error:nil];
  NSURLSession *session = [NSURLSession sessionWithConfiguration:config];
  [[session dataTaskWithRequest:request
              completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
                NSArray<NSHTTPCookie *> *cookies = @[];
                NSInteger status = [response isKindOfClass:NSHTTPURLResponse.class] ? ((NSHTTPURLResponse *)response).statusCode : 0;
                if (status == 200)
                  cookies = [NSHTTPCookie cookiesWithResponseHeaderFields:((NSHTTPURLResponse *)response).allHeaderFields forURL:url];
                dispatch_async(dispatch_get_main_queue(), ^{
                  if (status != 200) Log([NSString stringWithFormat:@"Signing the window in answered %ld.", (long)status]);
                  [session finishTasksAndInvalidate];
                  WKHTTPCookieStore *store = self.web.configuration.websiteDataStore.httpCookieStore;
                  dispatch_group_t group = dispatch_group_create();
                  for (NSHTTPCookie *cookie in cookies) {
                    dispatch_group_enter(group);
                    [store setCookie:cookie completionHandler:^{ dispatch_group_leave(group); }];
                  }
                  dispatch_group_notify(group, dispatch_get_main_queue(), ^{
                    self.signingIn = NO;
                    then();
                  });
                });
              }] resume];
}

- (void)userContentController:(WKUserContentController *)controller didReceiveScriptMessage:(WKScriptMessage *)message {
  if (!message.frameInfo.isMainFrame || ![message.body isEqual:@"signed-out"] || !self.base) return;
  if (![self sameOrigin:message.frameInfo.request.URL]) return;
  if (self.signingIn || (self.lastSignIn && -self.lastSignIn.timeIntervalSinceNow < 10)) return;  // Never a reload loop.
  Log(@"The dashboard's session ended; signing the window in again.");
  [self signInThen:^{
    [self.web reload];
  }];
}

- (BOOL)sameOrigin:(NSURL *)url {
  NSURL *base = self.base ? [NSURL URLWithString:self.base] : nil;
  return base && [url.scheme isEqual:base.scheme] && [url.host isEqual:base.host] && [url.port isEqual:base.port];
}

// ---- navigation --------------------------------------------------------------------------------------------------------

- (void)webView:(WKWebView *)webView
    decidePolicyForNavigationAction:(WKNavigationAction *)action
                    decisionHandler:(void (^)(WKNavigationActionPolicy))decide {
  NSURL *url = action.request.URL;
  NSString *scheme = url.scheme.lowercaseString;
  if ([scheme isEqual:@"axiom"]) {
    decide(WKNavigationActionPolicyCancel);
    if ([url.host isEqual:@"retry"]) {
      self.triedBoot = NO;
      self.mode = ModeSplash;
    } else if ([url.host isEqual:@"logs"]) {
      [self openLogs:nil];
    } else if ([url.host isEqual:@"diagnostics"]) {
      [self copyDiagnostics:nil];
    } else if ([url.host isEqual:@"reveal"]) {
      [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[ NSBundle.mainBundle.bundleURL ]];
    }
    return;
  }
  if ([scheme isEqual:@"about"] || [scheme isEqual:@"blob"] || [scheme isEqual:@"data"]) {
    decide(action.shouldPerformDownload ? WKNavigationActionPolicyDownload : WKNavigationActionPolicyAllow);
    return;
  }
  if ([self sameOrigin:url]) {
    decide(action.shouldPerformDownload ? WKNavigationActionPolicyDownload : WKNavigationActionPolicyAllow);
    return;
  }
  // This window shows the app and nothing else. A link to an employer's page opens in the person's own browser, and a
  // frame from anywhere else simply does not load.
  decide(WKNavigationActionPolicyCancel);
  if (!action.targetFrame || action.targetFrame.isMainFrame) [self openExternally:url];
}

// http, https, mail and phone links only. A file dropped outside a drop zone arrives here as a file:// navigation, and opening
// that in another app is not what anyone meant by dropping it.
- (void)openExternally:(NSURL *)url {
  NSString *scheme = url.scheme.lowercaseString;
  if (![@[ @"http", @"https", @"mailto", @"tel" ] containsObject:scheme ?: @""]) return;
  [self openForReal:url];
}

- (void)openForReal:(NSURL *)url {
  if ([self driveNoted:@"external" text:url.absoluteString]) return;
  [NSWorkspace.sharedWorkspace openURL:url];
}

- (void)webView:(WKWebView *)webView
    decidePolicyForNavigationResponse:(WKNavigationResponse *)response
                      decisionHandler:(void (^)(WKNavigationResponsePolicy))decide {
  NSString *disposition = nil;
  if ([response.response isKindOfClass:NSHTTPURLResponse.class])
    disposition = [((NSHTTPURLResponse *)response.response) valueForHTTPHeaderField:@"Content-Disposition"];
  BOOL attachment = [disposition.lowercaseString hasPrefix:@"attachment"];
  decide(response.canShowMIMEType && !attachment ? WKNavigationResponsePolicyAllow : WKNavigationResponsePolicyDownload);
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
  if (webView == self.web && !self.base && !self.splashLoaded) {
    self.splashLoaded = YES;
    if (self.splashKey) [self.web evaluateJavaScript:self.splashKey completionHandler:nil];
  }
  if (webView == self.web && self.base && !self.dashboardTimed) {
    self.dashboardTimed = YES;
    Log([NSString stringWithFormat:@"The dashboard loaded %.1f s after the window opened.", -self.launched.timeIntervalSinceNow]);
    if (!getenv("AXIOM_SHELL_DRIVE")) [self checkUpdates:NO];  // At most once a day, and silent unless there is something new.
  }
  [self snapshotForATest];
}

- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView {
  Log(@"The web view's process ended; reloading.");
  [webView reload];
}

// Proof that a test instance shows what it should, without the screen-recording permission a screenshot would need.
- (void)snapshotForATest {
  const char *prefix = getenv("AXIOM_SHELL_SNAPSHOT");
  if (!prefix) return;
  NSString *path = [NSString stringWithFormat:@"%s-%ld.png", prefix, (long)++self.snapshots];
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
    [self.web takeSnapshotWithConfiguration:nil
                          completionHandler:^(NSImage *image, NSError *error) {
                            NSBitmapImageRep *rep = image ? [NSBitmapImageRep imageRepWithData:image.TIFFRepresentation] : nil;
                            [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
                          }];
  });
}

// ---- what a web view does not do by itself: panels, new windows, file pickers, downloads ---------------------------------

- (WKWebView *)webView:(WKWebView *)webView
    createWebViewWithConfiguration:(WKWebViewConfiguration *)config
               forNavigationAction:(WKNavigationAction *)action
                    windowFeatures:(WKWindowFeatures *)features {
  NSURL *url = action.request.URL;
  // about:blank is how `window.open()` starts before it is given an address, and that address is then the one that matters.
  if (![self sameOrigin:url] && url.absoluteString.length && ![url.scheme isEqual:@"about"]) {
    [self openExternally:url];
    return nil;
  }
  NSWindow *extra = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1000, 800)
                                                styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                                          NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
                                                  backing:NSBackingStoreBuffered
                                                    defer:NO];
  extra.releasedWhenClosed = NO;
  extra.delegate = self;
  extra.title = @"AXIOM";
  WKWebView *web = [self webViewWithConfiguration:config];
  extra.contentView = web;
  [extra center];
  [extra makeKeyAndOrderFront:nil];
  [self.extraWindows addObject:extra];
  return web;
}

- (void)webViewDidClose:(WKWebView *)webView {
  if (webView != self.web) [webView.window performClose:nil];
}

- (void)webView:(WKWebView *)webView runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))done {
  NSAlert *alert = [NSAlert new];
  alert.messageText = message;
  [self driveAnswer:alert kind:@"alert"];
  [alert runModal];
  done();
}

- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))done {
  NSAlert *alert = [NSAlert new];
  alert.messageText = message;
  [alert addButtonWithTitle:@"OK"];
  [alert addButtonWithTitle:@"Cancel"];
  [self driveAnswer:alert kind:@"confirm"];
  done([alert runModal] == NSAlertFirstButtonReturn);
}

- (void)webView:(WKWebView *)webView runJavaScriptTextInputPanelWithPrompt:(NSString *)prompt defaultText:(NSString *)text initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSString *))done {
  NSAlert *alert = [NSAlert new];
  alert.messageText = prompt;
  NSTextField *field = [[NSTextField alloc] initWithFrame:NSMakeRect(0, 0, 320, 24)];
  field.stringValue = text ?: @"";
  alert.accessoryView = field;
  [alert addButtonWithTitle:@"OK"];
  [alert addButtonWithTitle:@"Cancel"];
  [self driveAnswer:alert kind:@"prompt"];
  done([alert runModal] == NSAlertFirstButtonReturn ? field.stringValue : nil);
}

// Uploading a resume.
- (void)webView:(WKWebView *)webView runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))done {
  NSOpenPanel *panel = [NSOpenPanel openPanel];
  panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
  panel.canChooseDirectories = parameters.allowsDirectories;
  panel.canChooseFiles = YES;
  [panel beginSheetModalForWindow:webView.window ?: self.window
                completionHandler:^(NSModalResponse result) {
                  NSString *chosen = self.driveFile;
                  self.driveFile = nil;
                  if (chosen) done(@[ [NSURL fileURLWithPath:chosen] ]);
                  else done(result == NSModalResponseOK ? panel.URLs : nil);
                }];
  [self driveOpenPanel:panel];
}

- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download {
  download.delegate = self;
}

- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download {
  download.delegate = self;
}

- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)name completionHandler:(void (^)(NSURL *))done {
  NSSavePanel *panel = [NSSavePanel savePanel];
  panel.nameFieldStringValue = name;
  [panel beginSheetModalForWindow:self.window
                completionHandler:^(NSModalResponse result) {
                  NSURL *target = result == NSModalResponseOK ? panel.URL : nil;
                  if (self.driveSaveDir) target = [NSURL fileURLWithPath:[self.driveSaveDir stringByAppendingPathComponent:name]];
                  self.driveSaveDir = nil;
                  if (target) [NSFileManager.defaultManager removeItemAtURL:target error:nil];
                  done(target);
                }];
  [self driveSavePanel:panel];
}

- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)data {
  Log([@"A download failed: " stringByAppendingString:error.localizedDescription]);
  NSAlert *alert = [NSAlert new];
  alert.messageText = @"The download did not finish";
  alert.informativeText = error.localizedDescription;
  [alert runModal];
}

// ---- windows -----------------------------------------------------------------------------------------------------------

- (BOOL)windowShouldClose:(NSWindow *)sender {
  if (sender != self.window) return YES;
  [sender orderOut:nil];  // Closing the window is not quitting: applications carry on, as the Dock's dot says.
  return NO;
}

- (void)windowWillClose:(NSNotification *)note {
  [self.extraWindows removeObject:note.object];
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
  [self showWindow:nil];
  return YES;
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app {
  return NO;
}

- (IBAction)showWindow:(id)sender {
  [self.window makeKeyAndOrderFront:nil];
  [NSApp activateIgnoringOtherApps:YES];
}

// ---- Quit: the same drain as `axiom stop` ------------------------------------------------------------------------------

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
  if (self.mode == ModeQuitting) return NSTerminateLater;
  if (!self.window) return NSTerminateNow;  // refuseWhereItIs: nothing was started.
  NSDictionary *state = ReadJSON(Native(@"state.json"));
  if (![self supervisorAlive:state]) {
    if (self.boot.running) [self.boot terminate];  // Still preparing, or downloading Chrome: nothing to drain.
    return NSTerminateNow;
  }
  NSInteger active = IntOr(state[@"active"], 0);
  // The system's own quit (log out, restart, shut down) carries a reason in the Apple event. Cancelling it here would make macOS
  // say AXIOM stopped the Mac from shutting down, and nobody is there to answer a question while it does.
  NSAppleEventDescriptor *event = NSAppleEventManager.sharedAppleEventManager.currentAppleEvent;
  if ([event paramDescriptorForKeyword:'why?'] || [event attributeDescriptorForKeyword:'why?']) self.quietQuit = YES;
  if (active > 0 && !self.quietQuit) {
    // Quit can arrive while another app is in front (a script, the Dock): the question must be seen, or the quit waits for it.
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = [NSString stringWithFormat:@"%ld application%@ in progress", (long)active, active == 1 ? @" is" : @"s are"];
    alert.informativeText = @"Quitting finishes whatever is being submitted, then stops. Nothing is sent twice, and anything "
                            @"unfinished carries on the next time you open AXIOM. This can take up to three minutes.";
    [alert addButtonWithTitle:@"Quit"];
    [alert addButtonWithTitle:@"Keep Running"];
    if ([alert runModal] != NSAlertFirstButtonReturn) {
      Log(@"Quit: kept running, as asked.");
      return NSTerminateCancel;
    }
  }
  // As long as a whole stop can take (supervisor.stop_budget), plus time to notice.
  NSInteger budget = 60;
  NSDictionary *children = state[@"children"];
  for (NSString *name in children) {
    if (IntOr(children[name][@"pid"], 0) > 0) budget += IntOr(children[name][@"grace"], 10) + 12;
  }
  self.quitDeadline = [NSDate dateWithTimeIntervalSinceNow:budget];
  self.mode = ModeQuitting;
  [self dismissBatteryWarning:nil];
  Log([NSString stringWithFormat:@"Quit: %ld active; waiting up to %ld s.", (long)active, (long)budget]);
  kill((pid_t)IntOr(state[@"pid"], 0), SIGTERM);
  self.base = nil;
  self.splashLoaded = NO;
  self.splashKey = nil;
  [self showSplash:@"Finishing up" message:@"Saving your work. AXIOM closes by itself." percent:nil kind:nil];
  if (!getenv("AXIOM_SHELL_BACKGROUND")) [self showWindow:nil];
  [self tick];
  return NSTerminateLater;
}

// ---- power: a sleeping Mac froze runs again and again (2026-10-19, 10-29) --------------------------------------------------

- (void)watchPower:(NSDictionary *)state alive:(BOOL)alive {
  NSDictionary *power = state[@"power"];
  BOOL battery = alive && [power isKindOfClass:NSDictionary.class] && [power[@"battery"] boolValue];
  if (!battery) {
    self.batteryWarned = NO;
    return;
  }
  if (IntOr(state[@"active"], 0) <= 0 || self.batteryWarned || self.mode == ModeQuitting) return;
  self.batteryWarned = YES;  // Once for each time the charger is pulled out, never again while it stays out.
  if ([NSUserDefaults.standardUserDefaults boolForKey:@"HideBatteryWarning"]) return;
  Log(@"Warned: applying on battery.");
  // Not a sheet: AppKit ignores Quit while one is attached, and a warning must never be what stops a safe stop.
  NSAlert *alert = [NSAlert new];
  alert.alertStyle = NSAlertStyleWarning;
  alert.messageText = @"Plug in your Mac while AXIOM applies";
  alert.informativeText = [NSString stringWithFormat:@"It is running on battery (%ld%%). If you close the lid or the battery runs out, "
                                                     @"your Mac goes to sleep and applications stop until it wakes. Plugged in "
                                                     @"with the lid open, they keep going.",
                                                     (long)IntOr(power[@"percent"], 0)];
  alert.showsSuppressionButton = YES;
  alert.suppressionButton.title = @"Don't remind me again";
  NSButton *ok = [alert addButtonWithTitle:@"OK"];
  ok.target = self;
  ok.action = @selector(dismissBatteryWarning:);
  [alert layout];
  [alert.window center];
  [alert.window orderFront:nil];
  self.batteryAlert = alert;
}

- (IBAction)dismissBatteryWarning:(id)sender {
  if (self.batteryAlert.suppressionButton.state == NSControlStateValueOn)
    [NSUserDefaults.standardUserDefaults setBool:YES forKey:@"HideBatteryWarning"];
  [self.batteryAlert.window orderOut:nil];
  self.batteryAlert = nil;
}

// ---- menus -------------------------------------------------------------------------------------------------------------

- (NSMenuItem *)item:(NSString *)title action:(SEL)action key:(NSString *)key {
  return [[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:key];
}

- (NSMenu *)addMenu:(NSString *)title to:(NSMenu *)bar {
  NSMenuItem *holder = [bar addItemWithTitle:title action:nil keyEquivalent:@""];
  NSMenu *menu = [[NSMenu alloc] initWithTitle:title];
  holder.submenu = menu;
  return menu;
}

- (void)buildMenus {
  NSMenu *bar = [NSMenu new];
  NSMenu *app = [self addMenu:@"AXIOM" to:bar];
  [app addItemWithTitle:@"About AXIOM" action:@selector(orderFrontStandardAboutPanel:) keyEquivalent:@""];
  [app addItem:NSMenuItem.separatorItem];
  self.loginItem = [app addItemWithTitle:@"Start at Login" action:@selector(toggleLogin:) keyEquivalent:@""];
  self.loginItem.target = self;
  [app addItem:NSMenuItem.separatorItem];
  [app addItemWithTitle:@"Hide AXIOM" action:@selector(hide:) keyEquivalent:@"h"];
  NSMenuItem *others = [app addItemWithTitle:@"Hide Others" action:@selector(hideOtherApplications:) keyEquivalent:@"h"];
  others.keyEquivalentModifierMask = NSEventModifierFlagOption | NSEventModifierFlagCommand;
  [app addItemWithTitle:@"Show All" action:@selector(unhideAllApplications:) keyEquivalent:@""];
  [app addItem:NSMenuItem.separatorItem];
  [app addItemWithTitle:@"Quit AXIOM" action:@selector(terminate:) keyEquivalent:@"q"];

  NSMenu *file = [self addMenu:@"File" to:bar];
  [file addItemWithTitle:@"Close Window" action:@selector(performClose:) keyEquivalent:@"w"];

  NSMenu *edit = [self addMenu:@"Edit" to:bar];  // Without these, Cmd-C and Cmd-V do nothing in a web view.
  [edit addItemWithTitle:@"Undo" action:@selector(undo:) keyEquivalent:@"z"];
  [edit addItemWithTitle:@"Redo" action:@selector(redo:) keyEquivalent:@"Z"];
  [edit addItem:NSMenuItem.separatorItem];
  [edit addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
  [edit addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
  [edit addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
  [edit addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];

  NSMenu *view = [self addMenu:@"View" to:bar];
  [view addItemWithTitle:@"Reload" action:@selector(reloadPage:) keyEquivalent:@"r"].target = self;
  [view addItem:NSMenuItem.separatorItem];
  [view addItemWithTitle:@"Actual Size" action:@selector(zoomReset:) keyEquivalent:@"0"].target = self;
  [view addItemWithTitle:@"Zoom In" action:@selector(zoomIn:) keyEquivalent:@"+"].target = self;
  [view addItemWithTitle:@"Zoom Out" action:@selector(zoomOut:) keyEquivalent:@"-"].target = self;
  [view addItem:NSMenuItem.separatorItem];
  NSMenuItem *full = [view addItemWithTitle:@"Enter Full Screen" action:@selector(toggleFullScreen:) keyEquivalent:@"f"];
  full.keyEquivalentModifierMask = NSEventModifierFlagControl | NSEventModifierFlagCommand;

  NSMenu *status = [self addMenu:@"Status" to:bar];
  status.delegate = self;
  status.autoenablesItems = NO;

  NSMenu *window = [self addMenu:@"Window" to:bar];
  [window addItemWithTitle:@"Minimize" action:@selector(performMiniaturize:) keyEquivalent:@"m"];
  [window addItemWithTitle:@"Zoom" action:@selector(performZoom:) keyEquivalent:@""];
  [window addItem:NSMenuItem.separatorItem];
  [window addItemWithTitle:@"AXIOM" action:@selector(showWindow:) keyEquivalent:@"0"].target = self;
  [window addItem:NSMenuItem.separatorItem];
  [window addItemWithTitle:@"Bring All to Front" action:@selector(arrangeInFront:) keyEquivalent:@""];
  NSApp.windowsMenu = window;

  NSMenu *help = [self addMenu:@"Help" to:bar];
  [help addItemWithTitle:@"Copy Diagnostics" action:@selector(copyDiagnostics:) keyEquivalent:@""].target = self;
  [help addItemWithTitle:@"Check for Updates\u2026" action:@selector(checkForUpdates:) keyEquivalent:@""].target = self;
  [help addItem:NSMenuItem.separatorItem];
  [help addItemWithTitle:@"Open Logs" action:@selector(openLogs:) keyEquivalent:@""].target = self;
  [help addItemWithTitle:@"Open Data Folder" action:@selector(openData:) keyEquivalent:@""].target = self;
  NSApp.helpMenu = help;

  NSApp.mainMenu = bar;
  [self refreshLoginCheck];
}

- (void)buildStatusItem {
  self.statusItem = [NSStatusBar.systemStatusBar statusItemWithLength:NSSquareStatusItemLength];
  // The logo's ring and dot, as a template image, so that it follows the menu bar's light and dark.
  NSImage *image = [NSImage imageWithSize:NSMakeSize(16, 16)
                                  flipped:NO
                           drawingHandler:^BOOL(NSRect rect) {
                             NSBezierPath *ring = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(1.6, 1.6, 12.8, 12.8)];
                             ring.lineWidth = 1.7;
                             [NSColor.blackColor set];
                             [ring stroke];
                             [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(5.3, 5.3, 5.4, 5.4)] fill];
                             return YES;
                           }];
  image.template = YES;
  self.statusItem.button.image = image;
  self.statusItem.button.toolTip = @"AXIOM";
  NSMenu *menu = [NSMenu new];
  menu.delegate = self;
  menu.autoenablesItems = NO;
  self.statusItem.menu = menu;
}

- (void)menuNeedsUpdate:(NSMenu *)menu {
  [menu removeAllItems];
  NSDictionary *state = ReadJSON(Native(@"state.json"));
  BOOL alive = [self supervisorAlive:state];
  NSDictionary *info = ReadJSON(Native(@"window.json"));
  NSMutableArray<NSString *> *lines = [NSMutableArray new];
  NSString *phase = state[@"phase"];
  [lines addObject:!alive ? @"AXIOM is stopped" : [phase isEqual:@"running"] ? @"AXIOM is running" : [phase isEqual:@"stopping"] ? @"AXIOM is finishing up" : @"AXIOM is starting"];
  if (alive) {
    NSInteger active = IntOr(state[@"active"], -1);
    [lines addObject:active < 0 ? @"Counting applications ..." : active == 0 ? @"No applications in progress" : [NSString stringWithFormat:@"%ld application%@ in progress", (long)active, active == 1 ? @"" : @"s"]];
    if ([state[@"sleep_prevented"] boolValue]) [lines addObject:@"Keeping this Mac awake while applying"];
    NSDictionary *power = state[@"power"];
    if ([power isKindOfClass:NSDictionary.class] && [power[@"battery"] boolValue])
      [lines addObject:[NSString stringWithFormat:@"On battery (%ld%%). Plug in before leaving it applying", (long)IntOr(power[@"percent"], 0)]];
  }
  if (info.count) {
    [lines addObject:[info[@"chrome_source"] isEqual:@"downloaded"] ? @"Applying through Chrome for Testing" : @"Applying through Google Chrome"];
  }
  for (NSString *line in lines) {
    NSMenuItem *row = [menu addItemWithTitle:line action:nil keyEquivalent:@""];
    row.enabled = NO;
  }
  [menu addItem:NSMenuItem.separatorItem];
  [menu addItemWithTitle:@"Open AXIOM" action:@selector(showWindow:) keyEquivalent:@""].target = self;
  [menu addItemWithTitle:@"Open Logs" action:@selector(openLogs:) keyEquivalent:@""].target = self;
  [menu addItemWithTitle:@"Open Data Folder" action:@selector(openData:) keyEquivalent:@""].target = self;
  if (menu == self.statusItem.menu) {
    [menu addItem:NSMenuItem.separatorItem];
    [menu addItemWithTitle:@"Quit AXIOM" action:@selector(terminate:) keyEquivalent:@""];
  }
}

- (void)refreshLoginCheck {
  NSString *path = [Home() stringByAppendingPathComponent:@"Library/LaunchAgents/app.axiom.desktop.plist"];
  self.loginItem.state = [NSFileManager.defaultManager fileExistsAtPath:path] ? NSControlStateValueOn : NSControlStateValueOff;
}

- (IBAction)toggleLogin:(id)sender {
  NSString *resources = NSBundle.mainBundle.resourcePath;
  NSTask *task = [NSTask new];
  task.executableURL = [NSURL fileURLWithPath:[resources stringByAppendingPathComponent:@"python/bin/python3"]];
  task.arguments = @[ @"-m", @"jobpilot.native.desktop", @"login-item", self.loginItem.state == NSControlStateValueOn ? @"off" : @"on" ];
  task.environment = @{
    @"PYTHONPATH" : [resources stringByAppendingPathComponent:@"app"],
    @"AXIOM_BUNDLE" : resources,
    @"HOME" : Home(),
    @"PYTHONDONTWRITEBYTECODE" : @"1",
    @"PYTHONSAFEPATH" : @"1",
  };
  task.currentDirectoryURL = [NSURL fileURLWithPath:resources];
  task.standardOutput = NSFileHandle.fileHandleWithNullDevice;
  task.standardError = NSFileHandle.fileHandleWithNullDevice;
  [task launchAndReturnError:nil];
  [task waitUntilExit];
  [self refreshLoginCheck];
}

- (IBAction)reloadPage:(id)sender {
  if (self.mode != ModeDashboard) return;
  [self signInThen:^{
    [self.web reload];
  }];
}

- (IBAction)zoomReset:(id)sender { self.web.pageZoom = 1.0; }
- (IBAction)zoomIn:(id)sender { self.web.pageZoom = MIN(self.web.pageZoom + 0.1, 2.0); }
- (IBAction)zoomOut:(id)sender { self.web.pageZoom = MAX(self.web.pageZoom - 0.1, 0.5); }
- (IBAction)openLogs:(id)sender { [self openForReal:[NSURL fileURLWithPath:Logs()]]; }
- (IBAction)openData:(id)sender { [self openForReal:[NSURL fileURLWithPath:Support()]]; }

// ---- help: diagnostics for the person who sent AXIOM, and whether a newer one exists ------------------------------------------

// Runs `python -m jobpilot.native.desktop <args>` and hands over what it printed. The same module serves a broken or stopped AXIOM,
// which is when anyone asks for diagnostics.
- (void)runDesktop:(NSArray<NSString *> *)arguments then:(void (^)(NSString *output))then {
  NSString *resources = NSBundle.mainBundle.resourcePath;
  NSTask *task = [NSTask new];
  task.executableURL = [NSURL fileURLWithPath:[resources stringByAppendingPathComponent:@"python/bin/python3"]];
  task.arguments = [@[ @"-m", @"jobpilot.native.desktop" ] arrayByAddingObjectsFromArray:arguments];
  task.environment = @{
    @"PYTHONPATH" : [resources stringByAppendingPathComponent:@"app"],
    @"AXIOM_BUNDLE" : resources,
    @"HOME" : Home(),
    @"PATH" : @"/usr/bin:/bin",
    @"PYTHONDONTWRITEBYTECODE" : @"1",
    @"PYTHONSAFEPATH" : @"1",
    @"OPENSSL_CONF" : [resources stringByAppendingPathComponent:@"openssl.cnf"],
  };
  task.currentDirectoryURL = [NSURL fileURLWithPath:resources];
  NSPipe *out = [NSPipe pipe];
  task.standardOutput = out;
  task.standardError = NSFileHandle.fileHandleWithNullDevice;
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
    NSString *text = @"";
    if ([task launchAndReturnError:nil]) {
      NSData *data = [out.fileHandleForReading readDataToEndOfFile];
      [task waitUntilExit];
      text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
    }
    dispatch_async(dispatch_get_main_queue(), ^{ then(text); });
  });
}

- (IBAction)copyDiagnostics:(id)sender {
  [self runDesktop:@[ @"diagnostics" ]
              then:^(NSString *text) {
                NSAlert *alert = [NSAlert new];
                if (text.length) {
                  [NSPasteboard.generalPasteboard clearContents];
                  [NSPasteboard.generalPasteboard setString:text forType:NSPasteboardTypeString];
                  alert.messageText = @"Diagnostics copied";
                  alert.informativeText = @"Paste it into a message to the person who gave you AXIOM (Command-V). It has no passwords, "
                                          @"keys, email addresses or mail in it.";
                } else {
                  alert.messageText = @"Could not collect diagnostics";
                  alert.informativeText = @"Use Help, Open Logs instead, and send the person who gave you AXIOM the file named supervisor.log.";
                }
                [self driveAnswer:alert kind:@"diagnostics"];
                [NSApp activateIgnoringOtherApps:YES];
                [alert runModal];
              }];
}

// A person has no other way to learn that a newer AXIOM exists. This only asks GitHub for the newest release's number and opens its
// page: it never downloads or replaces anything, and says nothing when there is nothing new, unless they asked.
- (IBAction)checkForUpdates:(id)sender { [self checkUpdates:YES]; }

- (void)checkUpdates:(BOOL)asked {
  NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
  if (!asked) {
    NSDate *last = [defaults objectForKey:@"LastUpdateCheck"];
    if (last && -last.timeIntervalSinceNow < 24 * 3600) return;
  }
  [defaults setObject:NSDate.date forKey:@"LastUpdateCheck"];
  [self runDesktop:@[ @"update-check" ]
              then:^(NSString *text) {
                NSData *data = [text dataUsingEncoding:NSUTF8StringEncoding];
                id parsed = data.length ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
                NSDictionary *answer = [parsed isKindOfClass:NSDictionary.class] ? parsed : @{};
                NSString *latest = answer[@"latest"], *url = answer[@"url"], *error = answer[@"error"];
                if ([answer[@"newer"] boolValue] && [url isKindOfClass:NSString.class]) {
                  if (!asked && [[defaults stringForKey:@"UpdateToldVersion"] isEqual:latest]) return;  // Told once; not every day.
                  [defaults setObject:latest forKey:@"UpdateToldVersion"];
                  Log([@"A newer version exists: " stringByAppendingString:latest]);
                  NSAlert *alert = [NSAlert new];
                  alert.messageText = [NSString stringWithFormat:@"AXIOM %@ is available", latest];
                  alert.informativeText = [NSString stringWithFormat:@"You have %@. Open the download page, get the new disk image, and drag AXIOM "
                                                                     @"over the old one in Applications. Your jobs and settings stay.",
                                                                     answer[@"current"] ?: @"an older version"];
                  NSButton *get = [alert addButtonWithTitle:@"Open the download page"];
                  NSButton *later = [alert addButtonWithTitle:@"Later"];
                  get.target = later.target = self;
                  get.action = @selector(updateAlertOpen:);
                  later.action = @selector(updateAlertLater:);
                  self.updateAlert = alert;
                  self.updateURL = url;
                  [alert layout];
                  [alert.window center];
                  [alert.window orderFront:nil];  // Not a sheet or a modal: Quit must always work (see the battery warning).
                } else if (asked) {
                  NSAlert *alert = [NSAlert new];
                  alert.messageText = error.length ? @"Could not check for updates" : @"AXIOM is up to date";
                  alert.informativeText = error.length ? [error stringByAppendingString:@" Check your internet connection and try again."]
                                                       : [NSString stringWithFormat:@"You have version %@, the newest.", answer[@"current"] ?: @""];
                  [self driveAnswer:alert kind:@"update"];
                  [NSApp activateIgnoringOtherApps:YES];
                  [alert runModal];
                }
              }];
}

- (IBAction)updateAlertOpen:(id)sender {
  NSURL *url = [NSURL URLWithString:self.updateURL ?: @""];
  [self.updateAlert.window orderOut:nil];
  self.updateAlert = nil;
  if (url) [self openExternally:url];
}

- (IBAction)updateAlertLater:(id)sender {
  [self.updateAlert.window orderOut:nil];
  self.updateAlert = nil;
}

// ---- the driver: how the parity list is proved in this very binary ---------------------------------------------------------
//
// With AXIOM_SHELL_DRIVE=<folder>, commands in <folder>/next.json run against the real window, its real panels and WebKit's real
// drop path, and the answer lands in result-<id>.json. Nothing here runs without that variable, which grants nothing: whoever can
// set it can already read the token file. Dialogs are really shown, and then answered, because nobody is there to click.

- (BOOL)driveNoted:(NSString *)what text:(NSString *)text {
  const char *dir = getenv("AXIOM_SHELL_DRIVE");
  if (!dir) return NO;
  NSString *path = [@(dir) stringByAppendingFormat:@"/%@.log", what];
  NSFileHandle *file = [NSFileHandle fileHandleForWritingAtPath:path];
  if (!file) {
    [NSFileManager.defaultManager createFileAtPath:path contents:nil attributes:nil];
    file = [NSFileHandle fileHandleForWritingAtPath:path];
  }
  [file seekToEndOfFile];
  [file writeData:[[text stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding]];
  [file closeFile];
  return YES;
}

- (void)driveAnswer:(NSAlert *)alert kind:(NSString *)kind {
  if (![self driveNoted:@"dialog" text:[NSString stringWithFormat:@"%@: %@", kind, alert.messageText]]) return;
  if (!alert.buttons.count) [alert addButtonWithTitle:@"OK"];
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 2), dispatch_get_main_queue(), ^{
    [alert.buttons.firstObject performClick:nil];
  });
}

- (void)driveOpenPanel:(NSOpenPanel *)panel {
  if (!self.driveFile) return;
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
    [self driveNoted:@"panel" text:[NSString stringWithFormat:@"open panel visible=%d", panel.isVisible]];
    [panel cancel:nil];  // NSOpenPanel has no way to choose a file from code. The completion hands over driveFile instead.
  });
}

- (void)driveSavePanel:(NSSavePanel *)panel {
  if (!self.driveSaveDir) return;
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
    [self driveNoted:@"panel" text:[NSString stringWithFormat:@"save panel visible=%d name=%@", panel.isVisible, panel.nameFieldStringValue]];
    [panel cancel:nil];  // On macOS 15 `ok:` from code leaves the panel open. The completion uses driveSaveDir instead.
  });
}

- (void)driveReply:(id)commandId value:(id)value error:(NSString *)error {
  NSDictionary *answer = @{@"value" : value ?: NSNull.null, @"error" : error ?: NSNull.null};
  if (![NSJSONSerialization isValidJSONObject:answer]) answer = @{@"value" : [value description] ?: @"", @"error" : NSNull.null};
  NSString *path = [@(getenv("AXIOM_SHELL_DRIVE")) stringByAppendingFormat:@"/result-%@.json", commandId];
  [[NSJSONSerialization dataWithJSONObject:answer options:0 error:nil] writeToFile:path atomically:YES];
}

- (NSMenuItem *)driveMenuItem:(NSString *)title in:(NSMenu *)menu {
  for (NSMenuItem *item in menu.itemArray) {
    if ([item.title isEqual:title] && item.action) return item;
    NSMenuItem *found = item.submenu ? [self driveMenuItem:title in:item.submenu] : nil;
    if (found) return found;
  }
  return nil;
}

- (void)driveTick {
  NSString *folder = @(getenv("AXIOM_SHELL_DRIVE"));
  NSString *path = [folder stringByAppendingPathComponent:@"next.json"];
  NSDictionary *command = ReadJSON(path);
  if (!command.count) return;
  [NSFileManager.defaultManager removeItemAtPath:path error:nil];
  id commandId = command[@"id"];
  NSString *op = command[@"op"];
  if ([op isEqual:@"js"]) {
    [self.web callAsyncJavaScript:command[@"body"]
                        arguments:@{}
                          inFrame:nil
                   inContentWorld:WKContentWorld.pageWorld
                completionHandler:^(id value, NSError *error) {
                  [self driveReply:commandId value:value error:error ? error.userInfo[@"WKJavaScriptExceptionMessage"] ?: error.localizedDescription : nil];
                }];
  } else if ([op isEqual:@"snapshot"]) {
    NSString *target = command[@"path"];
    [self.web takeSnapshotWithConfiguration:nil
                          completionHandler:^(NSImage *image, NSError *error) {
                            NSBitmapImageRep *rep = image ? [NSBitmapImageRep imageRepWithData:image.TIFFRepresentation] : nil;
                            BOOL ok = [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:target atomically:YES];
                            [self driveReply:commandId value:@(ok) error:nil];
                          }];
  } else if ([op isEqual:@"file"]) {  // The next open panel's answer.
    self.driveFile = command[@"path"];
    [self driveReply:commandId value:@YES error:nil];
  } else if ([op isEqual:@"save_to"]) {  // Where the next save panel's OK lands.
    self.driveSaveDir = command[@"path"];
    [self driveReply:commandId value:@YES error:nil];
  } else if ([op isEqual:@"key"]) {  // Through AppKit, not a synthetic DOM event: this is the path a keystroke takes.
    [self.window makeFirstResponder:self.web];
    for (NSNumber *type in @[ @(NSEventTypeKeyDown), @(NSEventTypeKeyUp) ]) {
      NSEvent *event = [NSEvent keyEventWithType:type.unsignedIntegerValue
                                        location:NSZeroPoint
                                   modifierFlags:NSEventModifierFlagCommand
                                       timestamp:NSProcessInfo.processInfo.systemUptime
                                    windowNumber:self.window.windowNumber
                                         context:nil
                                      characters:command[@"chars"]
                     charactersIgnoringModifiers:command[@"chars"]
                                       isARepeat:NO
                                         keyCode:(unsigned short)[command[@"code"] intValue]];
      [self.window sendEvent:event];
    }
    [self driveReply:commandId value:@YES error:nil];
  } else if ([op isEqual:@"status"]) {
    NSMenu *menu = self.statusItem.menu;
    [self menuNeedsUpdate:menu];
    [self driveReply:commandId value:[menu.itemArray valueForKey:@"title"] error:nil];
  } else if ([op isEqual:@"menu"]) {
    NSMenuItem *item = [self driveMenuItem:command[@"title"] in:NSApp.mainMenu];
    if (item) [NSApp sendAction:item.action to:item.target from:item];
    [self driveReply:commandId value:@{@"found" : @(item != nil), @"state" : @(item.state)} error:nil];
  } else if ([op isEqual:@"windows"]) {
    NSMutableArray *urls = [NSMutableArray new];
    for (NSWindow *extra in self.extraWindows) {
      WKWebView *web = (WKWebView *)extra.contentView;
      [urls addObject:web.URL.absoluteString ?: @""];
    }
    if ([command[@"close"] boolValue]) [[self.extraWindows copy] makeObjectsPerformSelector:@selector(performClose:) withObject:nil];
    [self driveReply:commandId value:urls error:nil];
  } else if ([op isEqual:@"pasteboard"]) {  // Read, or put back what a test replaced.
    NSPasteboard *board = NSPasteboard.generalPasteboard;
    NSString *before = [board stringForType:NSPasteboardTypeString] ?: @"";
    if (command[@"set"]) {
      [board clearContents];
      [board setString:command[@"set"] forType:NSPasteboardTypeString];
    }
    [self driveReply:commandId value:before error:nil];
  } else if ([op isEqual:@"front"]) {  // WebKit stops requestAnimationFrame in a covered window, and PDF.js draws on it.
    [self.window orderFrontRegardless];
    [self driveReply:commandId value:@(self.window.occlusionState & NSWindowOcclusionStateVisible) error:nil];
  } else if ([op isEqual:@"drop"]) {
    [self driveDrop:command[@"path"] x:[command[@"x"] doubleValue] y:[command[@"y"] doubleValue] reply:commandId];
  } else {
    [self driveReply:commandId value:nil error:@"unknown op"];
  }
}

// WebKit's own drop path, from a pasteboard holding the file, as a drag from Finder delivers it.
- (void)driveDrop:(NSString *)file x:(double)x y:(double)y reply:(id)commandId {
  NSPasteboard *board = [NSPasteboard pasteboardWithUniqueName];
  [board clearContents];
  [board writeObjects:@[ [NSURL fileURLWithPath:file] ]];
  DriveDrag *drag = [DriveDrag new];
  drag.board = board;
  drag.window = self.window;
  drag.at = NSMakePoint(x, self.web.bounds.size.height - y);
  id<NSDraggingDestination> web = (id<NSDraggingDestination>)self.web;
  [web draggingEntered:drag];
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 2), dispatch_get_main_queue(), ^{
    [web draggingUpdated:drag];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 2), dispatch_get_main_queue(), ^{
      NSDragOperation operation = [web draggingUpdated:drag];
      BOOL performed = [web prepareForDragOperation:drag] && [web performDragOperation:drag];
      [web concludeDragOperation:drag];
      [self driveReply:commandId value:@{@"operation" : @(operation), @"performed" : @(performed)} error:nil];
    });
  });
}

@end

int main(int argc, const char **argv) {
  @autoreleasepool {
    NSApplication *app = NSApplication.sharedApplication;
    AppDelegate *delegate = [AppDelegate new];
    app.delegate = delegate;
    app.activationPolicy = NSApplicationActivationPolicyRegular;
    [app run];
  }
  return 0;
}
