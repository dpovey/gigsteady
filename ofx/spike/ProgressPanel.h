// Native progress window (ProgressPanel.mm). progressOpen returns null off the main thread; every call accepts null.
#pragma once

struct ProgressPanel;
ProgressPanel* progressOpen(const char* title, const char* text);
bool progressStep(ProgressPanel* p, double fraction, const char* text);  // false once Cancel is pressed
void progressClose(ProgressPanel* p);
