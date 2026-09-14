using System.IO;
using System.Windows.Threading;
using EQRisk.Core.Engine;
using EQRisk.Desktop.Hosting;
using EQRisk.Desktop.Tray;
using EQRisk.Desktop.Views;
using EQRisk.Infrastructure;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EQRisk.Desktop.Tests;

/// <summary>
/// The app's service graph, resolved the way the app starts: on a WPF (STA) thread. A dependency cycle
/// through a factory registration gets past the container's own checks and hangs the start with no
/// window and no error, so everything is resolved against a deadline.
/// </summary>
public sealed class CompositionTests
{
    private static readonly TimeSpan Deadline = TimeSpan.FromSeconds(60);

    private sealed class NoEngine : IEngineLocator
    {
        public EngineCheck Check(string root) => EngineCheck.Fail("No engine in this test.");

        public EngineCheck Locate(string? configuredRoot, string appDirectory) => Check(appDirectory);
    }

    [Fact]
    public void Every_service_the_window_uses_resolves()
    {
        var root = Path.Combine(Path.GetTempPath(), "eqrisk-composition-" + Guid.NewGuid().ToString("N"));
        var resolved = new List<Type>();
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var services = new ServiceCollection();
                services.AddLogging();
                Composition.AddShared(services, new AppPaths(root).EnsureCreated());
                Composition.AddWindow(services, Dispatcher.CurrentDispatcher);
                services.Replace(ServiceDescriptor.Singleton<IEngineLocator, NoEngine>());   // never start the real engine

                var provider = services.BuildServiceProvider(new ServiceProviderOptions { ValidateOnBuild = true });
                try
                {
                    // MainWindow needs the app's resources; everything it takes is resolved here on its own.
                    var ours = services.Select(d => d.ServiceType)
                        .Where(t => !t.IsGenericTypeDefinition && t != typeof(MainWindow) &&
                                    t.Namespace?.StartsWith("EQRisk", StringComparison.Ordinal) == true)
                        .Distinct();
                    foreach (var type in ours)
                    {
                        Assert.NotEmpty(provider.GetServices(type));
                        resolved.Add(type);
                    }
                }
                finally
                {
                    provider.DisposeAsync().AsTask().GetAwaiter().GetResult();
                }
            }
            catch (Exception ex)
            {
                failure = ex;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.IsBackground = true;
        thread.Start();
        try
        {
            Assert.True(thread.Join(Deadline),
                $"Resolving the app's services hung after {resolved.Count} of them: a dependency cycle through a factory registration.");
            if (failure is not null)
            {
                Assert.Fail(failure.ToString());
            }

            Assert.Contains(typeof(TrayIconService), resolved);
            Assert.Contains(typeof(Presentation.Services.INotifier), resolved);
        }
        finally
        {
            SqliteConnection.ClearAllPools();
            try
            {
                Directory.Delete(root, recursive: true);
            }
            catch (IOException)
            {
                // A handle still closing; the temp folder is cleaned by Windows later.
            }
        }
    }
}
