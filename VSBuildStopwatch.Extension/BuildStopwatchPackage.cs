using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using EnvDTE;
using EnvDTE80;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using Task = System.Threading.Tasks.Task;

namespace VSBuildStopwatch
{
    [PackageRegistration(UseManagedResourcesOnly = true, AllowsBackgroundLoading = true)]
    [ProvideAutoLoad(UIContextGuids80.SolutionExists, PackageAutoLoadFlags.BackgroundLoad)]
    [Guid(PackageGuidString)]
    public sealed class BuildStopwatchPackage : AsyncPackage
    {
        public const string PackageGuidString = "47aca710-f18b-4c45-87d6-4854d95eca6a";
        private const string PipeName = "VSBuildStopwatch";
        private DTE2? _dte;
        private BuildEvents? _buildEvents;
        private Stopwatch? _stopwatch;
        private string? _activeBuildId;
        private readonly BlockingCollection<string> _messages = new BlockingCollection<string>();

        // A project/configuration can be built more than once in a solution build.
        // Keep a queue of event IDs per configuration so each Begin matches one Done.
        private readonly Dictionary<string, Queue<string>> _projectBuilds =
            new Dictionary<string, Queue<string>>(StringComparer.OrdinalIgnoreCase);

        protected override async Task InitializeAsync(CancellationToken cancellationToken,
            IProgress<ServiceProgressData> progress)
        {
            await JoinableTaskFactory.SwitchToMainThreadAsync(cancellationToken);
            _dte = await GetServiceAsync(typeof(EnvDTE.DTE)) as DTE2;
            if (_dte == null) return;
            _buildEvents = _dte.Events.BuildEvents;
            _buildEvents.OnBuildBegin += OnBuildBegin;
            _buildEvents.OnBuildDone += OnBuildDone;
            _buildEvents.OnBuildProjConfigBegin += OnBuildProjConfigBegin;
            _buildEvents.OnBuildProjConfigDone += OnBuildProjConfigDone;
            Task.Run(() => SendMessages());
            Debug.WriteLine("[VS Build Stopwatch] Solution and project build detector initialized.");
        }

        private void OnBuildBegin(vsBuildScope scope, vsBuildAction action)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            // Close any incomplete entries from an interrupted previous build.
            FinishPendingProjects(false);
            _activeBuildId = Guid.NewGuid().ToString("N");
            _stopwatch = Stopwatch.StartNew();
            string name = GetSolutionName();
            SendStart(_activeBuildId, name, "solution", null);
            Debug.WriteLine("[VS Build Stopwatch] SOLUTION START: " + name + " | " + action);
        }

        private void OnBuildProjConfigBegin(string Project, string ProjectConfig,
            string Platform, string SolutionConfig)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            string key = MakeProjectKey(Project, ProjectConfig, Platform, SolutionConfig);
            string id = Guid.NewGuid().ToString("N");
            if (!_projectBuilds.TryGetValue(key, out Queue<string>? pending))
            {
                pending = new Queue<string>();
                _projectBuilds.Add(key, pending);
            }
            pending.Enqueue(id);
            string name = ProjectDisplayName(Project);
            SendStart(id, name, "project", _activeBuildId);
            Debug.WriteLine("[VS Build Stopwatch] PROJECT START: " + name +
                " | Configuration: " + ProjectConfig + " | Platform: " + Platform);
        }

        private void OnBuildProjConfigDone(string Project, string ProjectConfig,
            string Platform, string SolutionConfig, bool Success)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            string key = MakeProjectKey(Project, ProjectConfig, Platform, SolutionConfig);
            if (!_projectBuilds.TryGetValue(key, out Queue<string>? pending) || pending.Count == 0)
            {
                Debug.WriteLine("[VS Build Stopwatch] PROJECT DONE without matching start: " + Project);
                return;
            }
            string id = pending.Dequeue();
            if (pending.Count == 0) _projectBuilds.Remove(key);
            SendFinish(id, Success);
            Debug.WriteLine("[VS Build Stopwatch] PROJECT FINISHED: " +
                ProjectDisplayName(Project) + " | Success: " + Success);
        }

        private void OnBuildDone(vsBuildScope scope, vsBuildAction action)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            FinishPendingProjects(false);
            if (_activeBuildId == null || _stopwatch == null) return;
            _stopwatch.Stop();
            bool success = _dte != null && _dte.Solution.SolutionBuild.LastBuildInfo == 0;
            SendFinish(_activeBuildId, success);
            Debug.WriteLine("[VS Build Stopwatch] SOLUTION FINISHED: " + GetSolutionName() +
                " | Duration: " + _stopwatch.Elapsed.ToString(@"hh\:mm\:ss\.fff") +
                " | Success: " + success);
            _activeBuildId = null;
            _stopwatch = null;
        }

        private void FinishPendingProjects(bool success)
        {
            foreach (Queue<string> pending in _projectBuilds.Values)
                while (pending.Count > 0) SendFinish(pending.Dequeue(), success);
            _projectBuilds.Clear();
        }

        private static string MakeProjectKey(string project, string config,
            string platform, string solutionConfig)
        {
            return project + "\u001f" + config + "\u001f" + platform + "\u001f" + solutionConfig;
        }

        private static string ProjectDisplayName(string project)
        {
            if (string.IsNullOrWhiteSpace(project)) return "Unknown Project";
            // DTE generally reports a project unique name or path, often with .csproj/.vcxproj.
            string name = project.TrimEnd('\\', '/');
            int separator = Math.Max(name.LastIndexOf('\\'), name.LastIndexOf('/'));
            if (separator >= 0) name = name.Substring(separator + 1);
            string extension = Path.GetExtension(name);
            if (extension.Equals(".csproj", StringComparison.OrdinalIgnoreCase) ||
                extension.Equals(".vbproj", StringComparison.OrdinalIgnoreCase) ||
                extension.Equals(".vcxproj", StringComparison.OrdinalIgnoreCase) ||
                extension.Equals(".fsproj", StringComparison.OrdinalIgnoreCase))
                name = Path.GetFileNameWithoutExtension(name);
            return string.IsNullOrEmpty(name) ? project : name;
        }

        private string GetSolutionName()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            string? fullName = _dte?.Solution?.FullName;
            return string.IsNullOrEmpty(fullName) ? "Unknown Solution" : Path.GetFileNameWithoutExtension(fullName);
        }

        private void SendStart(string id, string project, string kind, string? parent)
        {
            _messages.Add("{\"event\":\"start\",\"id\":\"" + id +
                "\",\"project\":\"" + JsonEscape(project) + "\",\"kind\":\"" + kind + "\"" +
                (parent == null ? "" : ",\"parent\":\"" + parent + "\"") + "}");
        }

        private void SendFinish(string id, bool success)
        {
            _messages.Add("{\"event\":\"finish\",\"id\":\"" + id +
                "\",\"success\":" + (success ? "true" : "false") + "}");
        }

        private static string JsonEscape(string value)
        {
            var result = new StringBuilder();
            foreach (char c in value)
            {
                switch (c)
                {
                    case '\\': result.Append("\\\\"); break;
                    case '"': result.Append("\\\""); break;
                    case '\n': result.Append("\\n"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (c < 32) result.Append("\\u" + ((int)c).ToString("x4"));
                        else result.Append(c);
                        break;
                }
            }
            return result.ToString();
        }

        private void SendMessages()
        {
            foreach (string message in _messages.GetConsumingEnumerable())
            {
                try
                {
                    using (var pipe = new NamedPipeClientStream(".", PipeName, PipeDirection.Out))
                    {
                        pipe.Connect(500);
                        byte[] bytes = Encoding.UTF8.GetBytes(message + "\n");
                        pipe.Write(bytes, 0, bytes.Length);
                        pipe.Flush();
                    }
                }
                catch (Exception ex)
                {
                    Debug.WriteLine("[VS Build Stopwatch] Tray app unavailable: " + ex.Message);
                }
            }
        }
    }
}
