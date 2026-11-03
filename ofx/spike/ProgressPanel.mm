// A small native progress window for long plugin actions (Resolve doesn't show the OFX progress suite for a button
// press). Runs as a modal session on the main thread: each step() pumps events so the bar draws and Cancel works.
// Off the main thread it does nothing and step() always continues.
#import <AppKit/AppKit.h>

#include "ProgressPanel.h"

// The development engine dylib holds a second copy of this class; a unique name per build avoids an Objective-C
// class clash with the plugin's copy and with earlier reloads.
#ifndef GS_CANCEL_CLASS
#define GS_CANCEL_CLASS GSCancelTarget
#endif
#define GSCancelTarget GS_CANCEL_CLASS

@interface GSCancelTarget : NSObject
@property(atomic) BOOL cancelled;
- (void)cancel:(id)sender;
@end

@implementation GSCancelTarget
- (void)cancel:(id)sender
{
    self.cancelled = YES;
}
@end

struct ProgressPanel {
    NSPanel* panel = nil;
    NSProgressIndicator* bar = nil;
    NSTextField* label = nil;
    GSCancelTarget* target = nil;
    NSModalSession session = nullptr;
};

ProgressPanel* progressOpen(const char* title, const char* text)
{
    if (![NSThread isMainThread]) return nullptr;
    auto* p = new ProgressPanel;
    NSRect frame = NSMakeRect(0, 0, 420, 120);
    p->panel = [[NSPanel alloc] initWithContentRect:frame
                                          styleMask:NSWindowStyleMaskTitled
                                            backing:NSBackingStoreBuffered
                                              defer:NO];
    p->panel.title = [NSString stringWithUTF8String:title];
    p->panel.releasedWhenClosed = NO;
    p->panel.level = NSModalPanelWindowLevel;
    NSView* v = p->panel.contentView;
    p->label = [NSTextField labelWithString:[NSString stringWithUTF8String:text]];
    p->label.frame = NSMakeRect(20, 78, 380, 20);
    [v addSubview:p->label];
    p->bar = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(20, 52, 380, 20)];
    p->bar.indeterminate = NO;
    p->bar.minValue = 0;
    p->bar.maxValue = 1;
    [v addSubview:p->bar];
    p->target = [GSCancelTarget new];
    NSButton* cancel = [NSButton buttonWithTitle:@"Cancel" target:p->target action:@selector(cancel:)];
    cancel.frame = NSMakeRect(310, 12, 90, 30);
    cancel.keyEquivalent = @"\033";
    [v addSubview:cancel];
    [p->panel center];
    p->session = [NSApp beginModalSessionForWindow:p->panel];
    [NSApp runModalSession:p->session];
    return p;
}

bool progressStep(ProgressPanel* p, double fraction, const char* text)
{
    if (!p) return true;
    p->bar.doubleValue = fraction;
    if (text) p->label.stringValue = [NSString stringWithUTF8String:text];
    [NSApp runModalSession:p->session];  // draws, and delivers the Cancel click
    return !p->target.cancelled;
}

void progressClose(ProgressPanel* p)
{
    if (!p) return;
    [NSApp endModalSession:p->session];
    [p->panel orderOut:nil];
    [p->panel close];
    delete p;
}
