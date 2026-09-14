using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using EQRisk.Core.Jobs;
using EQRisk.Presentation.Activity;

namespace EQRisk.Desktop.Views.Controls;

public partial class ActivityBar : UserControl
{
    private JobActivityViewModel? vm;

    public ActivityBar()
    {
        InitializeComponent();
        DataContextChanged += (_, _) => Attach(DataContext as JobActivityViewModel);
        Log.TextChanged += (_, _) => Log.ScrollToEnd();
    }

    private void Attach(JobActivityViewModel? next)
    {
        if (vm is not null)
        {
            vm.PropertyChanged -= OnChanged;
        }

        vm = next;
        if (vm is not null)
        {
            vm.PropertyChanged += OnChanged;
        }

        Sync();
    }

    private void OnChanged(object? sender, PropertyChangedEventArgs e) => Sync();

    // The step strip shows a daily update's steps; other jobs get an indeterminate bar while they run.
    private void Sync()
    {
        if (vm is null)
        {
            return;
        }

        Steps.Completed = vm.Job?.Status == JobStatus.Succeeded;
        Busy.Visibility = vm.IsRunning && !vm.ShowsSteps ? Visibility.Visible : Visibility.Collapsed;
    }
}
