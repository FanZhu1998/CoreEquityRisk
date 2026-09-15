using EQRisk.Presentation.Activity;
using SkiaSharp;

namespace EQRisk.Desktop.Tray;

/// <summary>
/// Draws the tray icon live with SkiaSharp: the EQRisk mark (the "E" and its two linked nodes, as in
/// app/assets/eqrisk-mark.svg) with a status dot in the corner, or a progress ring while a daily
/// update runs. Strokes are heavier than the SVG so the mark survives at 16 pixels.
/// </summary>
internal static class TrayIconRenderer
{
    /// <summary>Sizes Windows picks from for the notification area at 100% to 300% scaling.</summary>
    public static readonly int[] Sizes = [16, 20, 24, 32, 40, 48];

    private static readonly SKColor Page = SKColor.Parse("#080E18");
    private static readonly SKColor Accent = SKColor.Parse("#7DABDD");
    private static readonly SKColor Track = SKColor.Parse("#1A273A");

    public static SKColor ToneOf(Health health) => health switch
    {
        Health.UpToDate => SKColor.Parse("#46BF94"),
        Health.Pending => SKColor.Parse("#E2B86B"),
        Health.Problem => SKColor.Parse("#E5776C"),
        Health.Running => Accent,
        _ => SKColor.Parse("#7F92A9"),
    };

    /// <summary>One frame as PNG. <paramref name="progress"/> in [0, 1] draws the ring; null draws the dot.</summary>
    public static byte[] RenderPng(int size, Health health, double? progress)
    {
        var info = new SKImageInfo(size, size, SKColorType.Bgra8888, SKAlphaType.Premul);
        using var surface = SKSurface.Create(info);
        var canvas = surface.Canvas;
        canvas.Clear(SKColors.Transparent);
        canvas.Scale(size / 64f);

        using (var fill = new SKPaint { Color = Page, IsAntialias = true })
        {
            canvas.DrawRoundRect(new SKRect(0, 0, 64, 64), 13, 13, fill);
        }

        using (var stroke = new SKPaint
               {
                   Color = Accent, IsAntialias = true, Style = SKPaintStyle.Stroke, StrokeWidth = 6.5f, StrokeCap = SKStrokeCap.Round,
               })
        {
            canvas.DrawLine(15, 14, 15, 50, stroke);
            canvas.DrawLine(15, 14, 33, 14, stroke);
            canvas.DrawLine(15, 32, 29, 32, stroke);
            canvas.DrawLine(15, 50, 33, 50, stroke);
            canvas.DrawLine(44, 16, 46, 35, stroke);
        }

        using (var node = new SKPaint { Color = Accent, IsAntialias = true })
        {
            canvas.DrawCircle(44, 16, 5.5f, node);
        }

        var tone = ToneOf(health);
        using (var halo = new SKPaint { Color = Page, IsAntialias = true })
        {
            canvas.DrawCircle(47, 47, 17, halo);
        }

        if (progress is { } p)
        {
            var oval = new SKRect(35, 35, 59, 59);
            using var track = new SKPaint { Color = Track, IsAntialias = true, Style = SKPaintStyle.Stroke, StrokeWidth = 6 };
            using var arc = new SKPaint
            {
                Color = tone, IsAntialias = true, Style = SKPaintStyle.Stroke, StrokeWidth = 6, StrokeCap = SKStrokeCap.Round,
            };
            canvas.DrawOval(oval, track);
            canvas.DrawArc(oval, -90, (float)(Math.Clamp(p, 0.04, 1) * 360), false, arc);
        }
        else
        {
            using var dot = new SKPaint { Color = tone, IsAntialias = true };
            canvas.DrawCircle(47, 47, 12, dot);
        }

        using var image = surface.Snapshot();
        using var data = image.Encode(SKEncodedImageFormat.Png, 100);
        return data.ToArray();
    }

    /// <summary>PNG frames in an .ico container (the PNG-compressed icon format of Windows Vista onward).</summary>
    public static byte[] ToIco(IReadOnlyList<(int Size, byte[] Png)> frames)
    {
        ArgumentNullException.ThrowIfNull(frames);
        using var ms = new MemoryStream();
        using var w = new BinaryWriter(ms);
        w.Write((ushort)0);                         // reserved
        w.Write((ushort)1);                         // type: icon
        w.Write((ushort)frames.Count);
        var offset = 6 + 16 * frames.Count;
        foreach (var (size, png) in frames)
        {
            w.Write((byte)(size >= 256 ? 0 : size));  // width (0 means 256)
            w.Write((byte)(size >= 256 ? 0 : size));  // height
            w.Write((byte)0);                         // palette colours
            w.Write((byte)0);                         // reserved
            w.Write((ushort)1);                       // colour planes
            w.Write((ushort)32);                      // bits per pixel
            w.Write(png.Length);
            w.Write(offset);
            offset += png.Length;
        }

        foreach (var (_, png) in frames)
        {
            w.Write(png);
        }

        w.Flush();
        return ms.ToArray();
    }

    public static System.Drawing.Icon Icon(Health health, double? progress)
    {
        var frames = Sizes.Select(s => (s, RenderPng(s, health, progress))).ToList();
        using var ms = new MemoryStream(ToIco(frames));
        return new System.Drawing.Icon(ms);
    }
}
