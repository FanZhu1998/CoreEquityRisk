using System.Collections;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace EQRisk.Desktop.Views.Controls;

public partial class PageHeader : UserControl
{
    public PageHeader() => InitializeComponent();
}

/// <summary>One step of a strip: its number, name and state.</summary>
public sealed record StepItem(string Number, string Name, Brush Ring, Brush Fill, Brush Text, FontWeight Weight);

/// <summary>
/// A row of numbered steps: done in green, the current one in the accent, the rest muted. A daily
/// update shows its six; the Estimate page shows the model's five stages with none current.
/// </summary>
public partial class StepStrip : UserControl
{
    public static readonly DependencyProperty StepsProperty = DependencyProperty.Register(nameof(Steps),
        typeof(IEnumerable), typeof(StepStrip), new PropertyMetadata(null, Changed));

    public static readonly DependencyProperty CurrentProperty = DependencyProperty.Register(nameof(Current),
        typeof(int), typeof(StepStrip), new PropertyMetadata(-1, Changed));

    public static readonly DependencyProperty CompletedProperty = DependencyProperty.Register(nameof(Completed),
        typeof(bool), typeof(StepStrip), new PropertyMetadata(false, Changed));

    public static readonly DependencyProperty FailedProperty = DependencyProperty.Register(nameof(Failed),
        typeof(bool), typeof(StepStrip), new PropertyMetadata(false, Changed));

    public StepStrip()
    {
        InitializeComponent();
        Rebuild();
    }

    public IEnumerable? Steps
    {
        get => (IEnumerable?)GetValue(StepsProperty);
        set => SetValue(StepsProperty, value);
    }

    public int Current
    {
        get => (int)GetValue(CurrentProperty);
        set => SetValue(CurrentProperty, value);
    }

    public bool Completed
    {
        get => (bool)GetValue(CompletedProperty);
        set => SetValue(CompletedProperty, value);
    }

    public bool Failed
    {
        get => (bool)GetValue(FailedProperty);
        set => SetValue(FailedProperty, value);
    }

    private static void Changed(DependencyObject d, DependencyPropertyChangedEventArgs e) => ((StepStrip)d).Rebuild();

    private void Rebuild()
    {
        if (Items is null)
        {
            return;
        }

        Brush B(string key) => (Brush)FindResource(key);
        var names = Steps?.Cast<object>().Select(o => o.ToString() ?? "").ToList() ?? [];
        Items.ItemsSource = names.Select((name, i) =>
        {
            var done = Completed ? i <= Current : i < Current;
            var here = !Completed && i == Current;
            var failedHere = here && Failed;
            return new StepItem(
                (i + 1).ToString("00", System.Globalization.CultureInfo.InvariantCulture), name,
                failedHere ? B("BearBrush") : done ? B("BullBrush") : here ? B("AccentBrush") : B("Line2Brush"),
                failedHere ? B("BearDimBrush") : done ? B("BullDimBrush") : here ? B("AccentDimBrush") : Brushes.Transparent,
                done || here ? B("InkBrush") : B("MutedBrush"),
                here ? FontWeights.SemiBold : FontWeights.Normal);
        }).ToList();
    }
}
