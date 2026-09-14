using System.Data;
using System.Windows;
using System.Windows.Controls;
using EQRisk.Presentation.Pages;

namespace EQRisk.Desktop.Views.Pages;

// Code-behind for the pages. Views hold no logic beyond what XAML cannot say: the view models do the rest.

public partial class TodayView : UserControl
{
    public TodayView() => InitializeComponent();
}

public partial class DataView : UserControl
{
    public DataView() => InitializeComponent();

    // A ListBox cannot bind its selected items, so the chosen issues are copied to the view model here.
    private void OnIssuesChanged(object sender, SelectionChangedEventArgs e)
    {
        if (DataContext is not DataViewModel vm)
        {
            return;
        }

        vm.SelectedIssues.Clear();
        foreach (var item in Issues.SelectedItems.OfType<ExceptionCountRow>())
        {
            vm.SelectedIssues.Add(item.Issue);
        }

        vm.FilterExceptionsCommand.Execute(null);
    }
}

public partial class EstimateView : UserControl
{
    public EstimateView() => InitializeComponent();
}

public partial class ValidateView : UserControl
{
    public ValidateView() => InitializeComponent();
}

public partial class FactorReturnsView : UserControl
{
    public FactorReturnsView() => InitializeComponent();
}

public partial class FactorRiskView : UserControl
{
    public FactorRiskView() => InitializeComponent();
}

public partial class ExposuresView : UserControl
{
    public ExposuresView() => InitializeComponent();

    // Style columns arrive with the data; give them the table's number format and headers.
    private void OnColumn(object sender, DataGridAutoGeneratingColumnEventArgs e)
    {
        e.Column.Header = e.PropertyName.ToUpperInvariant();
        if (e.Column is DataGridTextColumn text && e.PropertyType == typeof(double))
        {
            text.Binding.StringFormat = "+0.000;-0.000;0.000";
            text.ElementStyle = (Style)FindResource("NumberCell");
            text.Width = new DataGridLength(96);
        }
        else if (e.Column is DataGridTextColumn label)
        {
            label.ElementStyle = (Style)FindResource("TextCell");
            label.Width = e.PropertyName == "Ticker" ? new DataGridLength(80) : new DataGridLength(190);
        }
        else if (e.PropertyType == typeof(bool))
        {
            e.Column.Width = new DataGridLength(60);
        }
    }
}

public partial class SpecificRiskView : UserControl
{
    public SpecificRiskView() => InitializeComponent();
}

public partial class PortfolioView : UserControl
{
    public PortfolioView() => InitializeComponent();
}

public partial class OptimizerView : UserControl
{
    public OptimizerView() => InitializeComponent();
}

public partial class JobsView : UserControl
{
    public JobsView() => InitializeComponent();
}

public partial class PublishView : UserControl
{
    public PublishView() => InitializeComponent();
}

public partial class SettingsView : UserControl
{
    public SettingsView() => InitializeComponent();
}

internal static class DataRowViews
{
    public static object? Cell(DataRowView row, string column) => row.Row.Table.Columns.Contains(column) ? row[column] : null;
}
