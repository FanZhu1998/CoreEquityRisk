using System.Reflection;
using System.Runtime.Versioning;
using EQRisk.Core.Jobs;
using EQRisk.Desktop.Tray;
using EQRisk.Desktop.Views;
using EQRisk.Infrastructure.Jobs;
using EQRisk.Presentation;
using EQRisk.Presentation.Activity;
using SkiaSharp;

namespace EQRisk.Desktop.Tests;

/// <summary>
/// The desktop app's layers, checked on the compiled assemblies (the C# twin of
/// tests/test_architecture.py): Core depends on nothing, view models never see WPF or the machine,
/// and only the Windows app puts the pieces together.
/// </summary>
public sealed class ArchitectureTests
{
    private static readonly Assembly Core = typeof(EngineJobs).Assembly;
    private static readonly Assembly Infrastructure = typeof(JobRunner).Assembly;
    private static readonly Assembly Presentation = typeof(PageViewModel).Assembly;
    private static readonly Assembly Desktop = typeof(Program).Assembly;

    private static readonly string[] WindowsUi = ["PresentationFramework", "PresentationCore", "WindowsBase", "System.Windows.Forms"];

    private static HashSet<string> References(Assembly a) =>
        a.GetReferencedAssemblies().Select(n => n.Name ?? "").ToHashSet(StringComparer.Ordinal);

    private static IEnumerable<string> Ours(Assembly a) => References(a).Where(n => n.StartsWith("EQRisk", StringComparison.Ordinal));

    [Fact]
    public void Core_depends_on_no_other_layer_and_no_ui()
    {
        Assert.Empty(Ours(Core));
        Assert.DoesNotContain(References(Core), WindowsUi.Contains);
    }

    [Fact]
    public void View_models_depend_only_on_core_and_never_on_wpf()
    {
        Assert.Equal(["EQRisk.Core"], Ours(Presentation));
        Assert.DoesNotContain(References(Presentation), WindowsUi.Contains);
    }

    [Fact]
    public void Infrastructure_depends_only_on_core_and_draws_nothing()
    {
        Assert.Equal(["EQRisk.Core"], Ours(Infrastructure));
        Assert.DoesNotContain(References(Infrastructure), WindowsUi.Contains);
    }

    [Fact]
    public void Only_the_windows_app_composes_the_layers() =>
        Assert.Equal(["EQRisk.Core", "EQRisk.Infrastructure", "EQRisk.Presentation"], Ours(Desktop).Order(StringComparer.Ordinal));

    [Fact]
    public void Core_and_view_models_are_plain_dotnet_not_windows_only()
    {
        foreach (var a in new[] { Core, Presentation })
        {
            var tfm = a.GetCustomAttribute<TargetFrameworkAttribute>()!.FrameworkName;
            Assert.Equal(".NETCoreApp,Version=v10.0", tfm);
            Assert.Null(a.GetCustomAttribute<SupportedOSPlatformAttribute>());
        }
    }
}

/// <summary>The tray icon is drawn, not shipped: check what it draws.</summary>
public sealed class TrayIconRendererTests
{
    private static SKColor PixelAt(byte[] png, double x, double y, int size)
    {
        using var bitmap = SKBitmap.Decode(png);
        Assert.Equal(size, bitmap.Width);
        return bitmap.GetPixel((int)(x / 64 * size), (int)(y / 64 * size));
    }

    [Theory]
    [InlineData(Health.UpToDate)]
    [InlineData(Health.Pending)]
    [InlineData(Health.Problem)]
    public void The_corner_dot_takes_the_health_colour(Health health)
    {
        var dot = PixelAt(TrayIconRenderer.RenderPng(64, health, null), 47, 47, 64);
        var want = TrayIconRenderer.ToneOf(health);
        Assert.Equal((want.Red, want.Green, want.Blue), (dot.Red, dot.Green, dot.Blue));
    }

    [Fact]
    public void A_running_update_draws_a_ring_not_a_dot()
    {
        var png = TrayIconRenderer.RenderPng(64, Health.Running, 0.5);
        var centre = PixelAt(png, 47, 47, 64);
        var ringTop = PixelAt(png, 47, 35, 64);
        Assert.Equal(SKColor.Parse("#080E18").Red, centre.Red);               // hollow: the page colour
        Assert.Equal(TrayIconRenderer.ToneOf(Health.Running).Blue, ringTop.Blue);
    }

    [Fact]
    public void Every_size_windows_asks_for_is_in_the_icon()
    {
        var frames = TrayIconRenderer.Sizes.Select(s => (s, TrayIconRenderer.RenderPng(s, Health.UpToDate, null))).ToList();
        var ico = TrayIconRenderer.ToIco(frames);
        Assert.Equal([0, 0, 1, 0], ico[..4]);
        Assert.Equal(TrayIconRenderer.Sizes.Length, BitConverter.ToUInt16(ico, 4));
        using var icon = TrayIconRenderer.Icon(Health.Pending, null);
        Assert.True(icon.Width > 0);
    }
}

public sealed class ConverterTests
{
    [Theory]
    [InlineData(null, false)]
    [InlineData(true, true)]
    [InlineData(false, false)]
    [InlineData("", false)]
    [InlineData("text", true)]
    [InlineData(0, false)]
    [InlineData(3, true)]
    public void Visible_when_there_is_something_to_show(object? value, bool visible)
    {
        var shown = new VisibleWhenConverter().Convert(value!, typeof(System.Windows.Visibility), null!, System.Globalization.CultureInfo.InvariantCulture);
        Assert.Equal(visible ? System.Windows.Visibility.Visible : System.Windows.Visibility.Collapsed, shown);
    }
}
