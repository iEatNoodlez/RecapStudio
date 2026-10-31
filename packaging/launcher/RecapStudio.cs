// Recap Studio.exe: starts the app with the Python that ships next to it (python\pythonw.exe -m recap), passing on
// any arguments (--browser, --no-window). Compiled by packaging\build.ps1 with the C# compiler that comes with
// Windows (.NET Framework 4), so it runs on any Windows 10 or 11 without installing anything.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(Path.Combine(root, "python"), "pythonw.exe");
        if (!File.Exists(pythonw) || !Directory.Exists(Path.Combine(root, "recap")))
        {
            MessageBox.Show("Recap Studio's files are missing from\n" + root + "\n\nReinstall it, or unzip the portable " +
                            "download again.", "Recap Studio", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        ProcessStartInfo info = new ProcessStartInfo(pythonw, "-m recap" + Quoted(args));
        info.WorkingDirectory = root;  // -m finds the recap package here
        info.UseShellExecute = false;
        // this Python only: nothing from another Python on the PC
        foreach (string name in new[] { "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV" })
        {
            info.EnvironmentVariables.Remove(name);
        }
        info.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        try
        {
            Process.Start(info);
        }
        catch (Exception e)
        {
            MessageBox.Show("Recap Studio couldn't start:\n" + e.Message, "Recap Studio", MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
            return 1;
        }
        return 0;
    }

    // the arguments as one command line, each quoted the way Windows programs split them again
    static string Quoted(string[] args)
    {
        StringBuilder line = new StringBuilder();
        foreach (string arg in args)
        {
            line.Append(' ');
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            {
                line.Append(arg);
                continue;
            }
            line.Append('"');
            int slashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\') { slashes++; continue; }
                line.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
                slashes = 0;
                line.Append(c);
            }
            line.Append('\\', slashes * 2).Append('"');
        }
        return line.ToString();
    }
}
