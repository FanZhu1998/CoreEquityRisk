using System.Diagnostics;

namespace EQRisk.Infrastructure.Native;

/// <summary>
/// Windows gives a child the idle priority class of an idle parent. The app lowers its own priority
/// in efficiency mode while its window is hidden, so every engine process is set back to normal as it
/// starts: a daily update must not crawl because nobody is looking at the window.
/// </summary>
internal static class ProcessPriority
{
    public static void Normal(Process process)
    {
        try
        {
            process.PriorityClass = ProcessPriorityClass.Normal;
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            // It already exited; its end is handled by whoever watches it.
        }
    }
}
