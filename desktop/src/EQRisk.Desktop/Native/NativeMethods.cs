using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;

namespace EQRisk.Desktop.Native;

/// <summary>
/// The window frame through DWM: dark mode, a title bar and border in the app's navy, and rounded
/// corners. Windows 10 honours dark mode; Windows 11 also takes the colours and the corners.
/// Unsupported attributes are ignored, never fatal.
/// </summary>
internal static partial class WindowEffects
{
    private const int UseImmersiveDarkMode = 20;
    private const int WindowCornerPreference = 33;
    private const int BorderColor = 34;
    private const int CaptionColor = 35;
    private const int TextColor = 36;
    private const int CornerRound = 2;
    private const int CornerRoundSmall = 3;

    /// <summary>Paint the frame in the palette's page, border and ink colours.</summary>
    public static void ApplyFrame(Window window, bool smallCorners = false)
    {
        ArgumentNullException.ThrowIfNull(window);
        var hwnd = new WindowInteropHelper(window).EnsureHandle();
        Set(hwnd, UseImmersiveDarkMode, 1);
        Set(hwnd, CaptionColor, ColorRef(Palette("PageColor")));
        Set(hwnd, BorderColor, ColorRef(Palette("Surface3Color")));
        Set(hwnd, TextColor, ColorRef(Palette("InkColor")));
        Set(hwnd, WindowCornerPreference, smallCorners ? CornerRoundSmall : CornerRound);
    }

    private static Color Palette(string key) =>
        Application.Current?.TryFindResource(key) is Color c ? c : Colors.Black;

    // COLORREF is 0x00BBGGRR.
    private static int ColorRef(Color c) => c.R | (c.G << 8) | (c.B << 16);

    private static void Set(nint hwnd, int attribute, int value) =>
        _ = DwmSetWindowAttribute(hwnd, attribute, ref value, sizeof(int));

    [LibraryImport("dwmapi.dll")]
    private static partial int DwmSetWindowAttribute(nint hwnd, int attribute, ref int value, int size);
}

/// <summary>
/// Efficiency mode (Windows 11's leaf in Task Manager): EcoQoS execution-speed throttling plus the
/// idle priority class, while the window is hidden. Engine processes are set back to normal
/// priority when they start, so a job never inherits the throttle.
/// </summary>
internal static partial class EfficiencyMode
{
    private const int ProcessPowerThrottling = 4;
    private const uint CurrentVersion = 1;
    private const uint ExecutionSpeed = 0x1;

    public static void Set(bool on)
    {
        var state = new PowerThrottlingState { Version = CurrentVersion, ControlMask = ExecutionSpeed, StateMask = on ? ExecutionSpeed : 0 };
        _ = SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling, ref state,
            (uint)Marshal.SizeOf<PowerThrottlingState>());
        try
        {
            using var me = Process.GetCurrentProcess();
            me.PriorityClass = on ? ProcessPriorityClass.Idle : ProcessPriorityClass.Normal;
        }
        catch (Win32Exception)
        {
            // Not permitted in this session; throttling alone still applies.
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PowerThrottlingState
    {
        public uint Version;
        public uint ControlMask;
        public uint StateMask;
    }

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetProcessInformation(nint process, int infoClass, ref PowerThrottlingState info, uint size);

    [LibraryImport("kernel32.dll")]
    private static partial nint GetCurrentProcess();
}

/// <summary>Where the taskbar is, to put the tray flyout next to it like Windows' own flyouts.</summary>
internal static partial class Taskbar
{
    private const uint GetTaskbarPosition = 5;
    private const uint NoSize = 0x0001;
    private const uint NoZOrder = 0x0004;
    private const uint NoActivate = 0x0010;

    public enum Edge
    {
        Left = 0,
        Top = 1,
        Right = 2,
        Bottom = 3,
    }

    /// <summary>Place <paramref name="window"/> against the taskbar, near the cursor, inside the screen.</summary>
    public static void PlaceNearTray(Window window, int margin)
    {
        ArgumentNullException.ThrowIfNull(window);
        var hwnd = new WindowInteropHelper(window).EnsureHandle();
        if (!GetWindowRect(hwnd, out var own) || !GetCursorPos(out var cursor))
        {
            return;
        }

        int width = own.Right - own.Left, height = own.Bottom - own.Top;
        var bar = new AppBarData { Size = (uint)Marshal.SizeOf<AppBarData>() };
        var edge = SHAppBarMessage(GetTaskbarPosition, ref bar) != 0 ? (Edge)bar.Edge : Edge.Bottom;
        var r = bar.Rect;
        var (x, y) = edge switch
        {
            Edge.Top => (cursor.X - width / 2, r.Bottom + margin),
            Edge.Left => (r.Right + margin, cursor.Y - height / 2),
            Edge.Right => (r.Left - width - margin, cursor.Y - height / 2),
            _ => (cursor.X - width / 2, r.Top - height - margin),
        };
        var screen = SystemParameters.VirtualScreenWidth > 0 ? ScreenOf(cursor) : r;
        x = Math.Clamp(x, screen.Left + margin, Math.Max(screen.Left + margin, screen.Right - width - margin));
        y = Math.Clamp(y, screen.Top + margin, Math.Max(screen.Top + margin, screen.Bottom - height - margin));
        _ = SetWindowPos(hwnd, 0, x, y, 0, 0, NoSize | NoZOrder | NoActivate);
    }

    private static Rect32 ScreenOf(Point32 point)
    {
        var monitor = MonitorFromPoint(point, 2);           // MONITOR_DEFAULTTONEAREST
        var info = new MonitorInfo { Size = (uint)Marshal.SizeOf<MonitorInfo>() };
        return GetMonitorInfoW(monitor, ref info) ? info.Work : default;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect32
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Point32
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct AppBarData
    {
        public uint Size;
        public nint Hwnd;
        public uint CallbackMessage;
        public uint Edge;
        public Rect32 Rect;
        public nint Param;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo
    {
        public uint Size;
        public Rect32 Monitor;
        public Rect32 Work;
        public uint Flags;
    }

    [LibraryImport("shell32.dll")]
    private static partial nuint SHAppBarMessage(uint message, ref AppBarData data);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetCursorPos(out Point32 point);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetWindowRect(nint hwnd, out Rect32 rect);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetWindowPos(nint hwnd, nint after, int x, int y, int cx, int cy, uint flags);

    [LibraryImport("user32.dll")]
    private static partial nint MonitorFromPoint(Point32 point, uint flags);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetMonitorInfoW(nint monitor, ref MonitorInfo info);
}

/// <summary>
/// One EQRisk window per Windows session. A second start signals the first to come forward and
/// exits; the scheduled headless run is not an instance and does not take part.
/// </summary>
internal sealed class SingleInstance : IDisposable
{
    private const string MutexName = @"Local\EQRisk.Desktop.Instance";
    private const string EventName = @"Local\EQRisk.Desktop.Activate";

    private readonly Mutex mutex;
    private readonly EventWaitHandle activate;
    private volatile bool disposed;

    private SingleInstance()
    {
        mutex = new Mutex(initiallyOwned: true, MutexName, out var created);
        IsFirst = created;
        activate = new EventWaitHandle(false, EventResetMode.AutoReset, EventName);
    }

    public bool IsFirst { get; }

    public static SingleInstance Acquire() => new();

    public void ActivateFirst() => activate.Set();

    /// <summary>Call <paramref name="onActivate"/> whenever another start asks this one to come forward.</summary>
    public void Listen(Action onActivate)
    {
        var thread = new Thread(() =>
        {
            try
            {
                while (activate.WaitOne() && !disposed)
                {
                    onActivate();
                }
            }
            catch (ObjectDisposedException)
            {
                // Shutting down.
            }
        })
        {
            IsBackground = true,
            Name = "EQRisk activation",
        };
        thread.Start();
    }

    public void Dispose()
    {
        disposed = true;
        if (IsFirst)
        {
            mutex.ReleaseMutex();
        }

        mutex.Dispose();
        activate.Dispose();
    }
}
