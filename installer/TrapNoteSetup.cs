// TrapNote のインストーラ（TrapNote-Setup-<版>.exe）。Windows に入っている .NET Framework 4 の C# コンパイラ（csc.exe、C# 5）で作る。作り方は build_installer.py。
//
// すること（インストール.bat と同じ。bat を使わないのは、日本語の bat が、日本語でない Windows では文字化けして動かないことがあるため）:
//   1. この exe の中に入っている TrapNote のプログラム（payload.zip）を、選んだフォルダに置く（data フォルダ = 設定・ログ・モデル には触らない）
//   2. conda を探す。使うのは、前にこのインストーラが入れた <フォルダ>\miniforge3 か、環境 trapnote がもうある conda（インストール.bat で入れた PC）だけ。
//      どちらもなければ、Miniforge（conda-forge が配っている conda）を取ってきて、<フォルダ>\miniforge3 に入れる（PATH やレジストリには書かない）。
//      環境のない Anaconda などに新しく作らないのは、PC ごとに conda の版や設定（配布元 defaults の利用規約の確認など）が違って、失敗のしかたが変わるため
//   3. conda 環境 trapnote を作る。env\trapnote-env.yml を読んで、conda で Python を入れ（配布元は conda-forge だけ）、pip でモジュールを入れる。
//      もうあるときは、足りないモジュールだけを pip で足す（入っていて条件に合うものの版は変えない）。
//      conda env create / update -f を使わないのは、(a) pip の部分の出力が終わるまで出ないので、PyTorch を取ってくる十数分の間、止まって見える、
//      (b) pip の部分を pip install -U で流すので、もうある環境の torch などが新しい版に変わってしまう、(c) 利用者の .condarc の配布元が混ざる、ため
//   4. MegaDetector を依存なしで入れる。NVIDIA の GPU があるのに torch が CPU 版なら、CUDA 版に入れ直す
//   5. デスクトップにアイコンを作る（TrapNote。選べば TrapNote Lite も）
// 記録は <フォルダ>\install.log に残る。
//
// 引数（試験と、画面なしで入れるとき用）: /silent（画面を出さない。終了コード 0 = できた）  /dir=<フォルダ>  /ownconda（もとからある環境 trapnote を使わず、専用の Miniforge に作る）
//   /lite（Lite のアイコンも作る）  /noshortcut（アイコンを作らない）  /dryrun（プログラムを置くだけ。conda と pip は、流すはずの命令を記録に書くだけ）  /lang=ja|en
//   /probe（何も入れずに、この PC の conda と環境 trapnote を調べて記録に書くだけ。命令の組み立てを確かめる用）
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("TrapNote Setup")]
[assembly: AssemblyProduct("TrapNote")]
[assembly: AssemblyDescription("TrapNote installer")]
[assembly: AssemblyCompany("TrapNote")]
[assembly: AssemblyVersion("0.3.0.0")]
[assembly: AssemblyFileVersion("0.3.0.0")]

namespace TrapNoteSetup
{
    class Options
    {
        public string Dir = @"C:\TrapNote";
        public bool Silent, OwnConda, Lite, NoShortcut, DryRun, Probe;
    }

    static class Program
    {
        public static bool Ja = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "ja";
        public const string EnvName = "trapnote";
        public const string MiniforgeUrl = "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe";

        public static string T(string ja, string en) { return Ja ? ja : en; }

        [STAThread]
        static int Main(string[] args)
        {
            Options o = new Options();
            foreach (string a0 in args)
            {
                string a = a0.Trim();
                string low = a.ToLowerInvariant();
                if (low == "/silent") o.Silent = true;
                else if (low == "/ownconda") o.OwnConda = true;
                else if (low == "/lite") o.Lite = true;
                else if (low == "/noshortcut") o.NoShortcut = true;
                else if (low == "/dryrun") o.DryRun = true;
                else if (low == "/probe") o.Probe = true;
                else if (low.StartsWith("/dir=")) o.Dir = a.Substring(5).Trim('"');
                else if (low == "/lang=ja") Ja = true;
                else if (low == "/lang=en") Ja = false;
            }
            if (o.Silent)
            {
                string why = Installer.CheckDir(o.Dir);
                if (why != null) return 2;
                Installer inst = new Installer(o, delegate(string s) { });
                return inst.Run() ? 0 : 1;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm(o));
            return 0;
        }

        public static string Version()
        {
            // payload.zip の中の VERSION.txt（build_installer.py が書く: 版と、プログラムの日時）
            try
            {
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
                using (ZipArchive zip = new ZipArchive(s, ZipArchiveMode.Read))
                {
                    ZipArchiveEntry e = zip.GetEntry("VERSION.txt");
                    if (e != null) using (StreamReader r = new StreamReader(e.Open(), Encoding.UTF8)) return r.ReadToEnd().Trim();
                }
            }
            catch (Exception) { }
            return "";
        }
    }

    /// <summary>インストールの手順そのもの（画面とは別のスレッドで動く）。log に 1 行ずつ出し、install.log にも書く</summary>
    class Installer
    {
        readonly Options o;
        readonly Action<string> ui;
        StreamWriter logFile;
        public string EnvPython = "";
        public string CondaRoot = "";
        public string Error = "";

        public Installer(Options o, Action<string> ui) { this.o = o; this.ui = ui; }

        /// <summary>フォルダの名前が使えるか。使えなければ理由（使えるなら null）</summary>
        public static string CheckDir(string dir)
        {
            if (string.IsNullOrEmpty(dir)) return Program.T("フォルダを指定してください。", "Choose a folder.");
            if (!Regex.IsMatch(dir, @"^[A-Za-z]:\\[A-Za-z0-9_.\-\\]+$") || dir.EndsWith("\\") || dir.Contains("\\\\"))
                return Program.T("フォルダの名前は、半角の英数字と _ - . だけにしてください（空白や日本語が入ると、conda が動かないことがあります）。例: C:\\TrapNote",
                                 "Use only ASCII letters, digits and _ - . in the folder path (spaces and non-ASCII characters can break conda). Example: C:\\TrapNote");
            if (dir.Length > 80) return Program.T("フォルダの名前が長すぎます（80 文字まで）。", "The folder path is too long (80 characters at most).");
            string low = dir.ToLowerInvariant();
            if (low.StartsWith(@"c:\windows") || low.StartsWith(@"c:\program")) return Program.T("Windows や Program Files の中には入れられません。", "Do not install inside Windows or Program Files.");
            return null;
        }

        void Log(string s)
        {
            try { if (logFile != null) { logFile.WriteLine(s); logFile.Flush(); } } catch (Exception) { }
            ui(s);
        }

        void Head(string s) { Log(""); Log("=== " + s + " ==="); }

        public bool Run()
        {
            try
            {
                Directory.CreateDirectory(o.Dir);
                logFile = new StreamWriter(Path.Combine(o.Dir, "install.log"), true, new UTF8Encoding(true));
                Log("");
                Log("######## TrapNote Setup " + Program.Version() + "  " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " ########");
                Log("folder: " + o.Dir + (o.DryRun ? "  [dryrun]" : "") + (o.OwnConda ? "  [ownconda]" : "") + (o.Lite ? "  [lite]" : "") + (o.NoShortcut ? "  [noshortcut]" : ""));

                if (o.Probe) return Probe();

                Head(Program.T("1/5 プログラムを置く", "1/5 Copying the program"));
                int n = Extract();
                Log(Program.T("置いたファイル: ", "files written: ") + n);

                Head(Program.T("2/5 conda を探す", "2/5 Looking for conda"));
                bool exists;
                CondaRoot = FindConda(out exists);
                if (!exists)
                {
                    // 新しく作るときは、空きを先に見る（Miniforge 0.4 GB + 環境 5〜6 GB + 取ってくる途中のファイル 3 GB ほど）
                    double free = FreeGb(o.Dir);
                    Log(Program.T("ディスクの空き: ", "free disk space: ") + free.ToString("0.0", CultureInfo.InvariantCulture) + " GB");
                    if (!o.DryRun && free >= 0 && free < 9)
                        throw new Exception(Program.T("ディスクの空きが足りません（新しく入れるには 10 GB ほど要ります。いまの空きは ", "Not enough free disk space (about 10 GB is needed; free now: ")
                                            + free.ToString("0.0", CultureInfo.InvariantCulture) + " GB" + Program.T("）。", ")."));
                }
                if (CondaRoot == "")
                {
                    Log(Program.T("専用の Miniforge を入れます。", "Installing a private Miniforge."));
                    CondaRoot = InstallMiniforge();
                }
                Log("conda: " + CondaRoot);

                Head(Program.T("3/5 Python の環境（trapnote）を作る。十数分かかります", "3/5 Creating the Python environment (trapnote). This takes 10-20 minutes"));
                string act = "call \"" + Path.Combine(CondaRoot, "Scripts", "activate.bat") + "\" \"" + CondaRoot + "\"";
                string yml = Path.Combine(o.Dir, "env", "trapnote-env.yml");
                string env = act + " && conda activate " + Program.EnvName;
                EnvSpec spec = ParseEnvYml(File.Exists(yml) ? File.ReadAllLines(yml, Encoding.UTF8) : new string[0]);
                if (!spec.Ok) throw new Exception(Program.T("env\\trapnote-env.yml から、要るモジュールの一覧を読めませんでした。", "Could not read the module list from env\\trapnote-env.yml."));
                string req = WriteRequirements(spec);
                if (exists)
                {
                    // もうある環境には、足りないモジュールだけを足す。pip install -r は、入っていて条件に合うものの版を変えない
                    Log(Program.T("環境 trapnote があるので、足りないモジュールだけを足します（入っているものの版は変えません）。",
                                  "The environment trapnote exists. Adding only the missing modules (installed versions are kept)."));
                    Must(env + " && python -m pip install -r \"" + req + "\"", Program.T("足りないモジュールを入れられませんでした。", "Could not install the missing modules."));
                }
                else
                {
                    Log(Program.T("環境 trapnote を作ります（まず Python。1〜2 分）。", "Creating the environment trapnote (Python first; 1-2 minutes)."));
                    Must(act + " && " + CondaCreate(spec), Program.T("環境を作れませんでした。", "Could not create the environment."));
                    Log(Program.T("モジュールを入れます。PyTorch（3 GB ほど）を取ってくる間は、数分〜十数分、表示が止まります。そのまま待ってください。",
                                  "Installing the modules. While PyTorch (about 3 GB) is downloaded, nothing is printed for several minutes. Please wait."));
                    // --no-cache-dir: 取ってきたファイルの控え（3 GB 以上）を、利用者のフォルダに残さない
                    Must(env + " && python -m pip install --no-cache-dir -r \"" + req + "\"",
                         Program.T("モジュールを入れられませんでした（ネットワークを確かめて、もう一度実行してください。入ったところの続きから始まります）。",
                                   "Could not install the modules (check the network and run the installer again; it continues from where it stopped)."));
                }
                if (!o.DryRun)
                {
                    List<string> outp = new List<string>();
                    Cmd(env + " && python -c \"import sys;print(sys.executable)\"", outp, false);
                    foreach (string line in outp) if (line.Trim().ToLowerInvariant().EndsWith("python.exe")) EnvPython = line.Trim();
                    if (EnvPython == "" || !File.Exists(EnvPython)) throw new Exception(Program.T("環境 trapnote の Python が見つかりません。", "The Python of the environment trapnote was not found."));
                    Log("python: " + EnvPython);
                }

                Head(Program.T("4/5 MegaDetector などを入れる", "4/5 Installing MegaDetector etc."));
                // megadetector 本体は、依存の衝突（ultralytics-yolov5 と yolov5）を避けるため、依存なしで入れる（インストール.bat と同じ）
                Must(env + " && python -m pip install --no-deps megadetector==10.0.28", Program.T("MegaDetector を入れられませんでした（ネットワークを確かめてください）。", "Could not install MegaDetector (check the network)."));
                Must(env + " && python -m pip install dill send2trash", Program.T("追加のモジュールを入れられませんでした。", "Could not install the extra modules."));
                if (!o.DryRun)
                {
                    // NVIDIA の GPU があるのに torch が CPU 版なら、PyTorch の CUDA 12.8 用の置き場から入れ直す（pip が PyPI の CPU 版を選ぶことがあるため）
                    bool nvidia = Cmd("where nvidia-smi", null, false) == 0;
                    bool cuda = Cmd(env + " && python -c \"import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)\"", null, false) == 0;
                    if (nvidia && !cuda)
                    {
                        Log(Program.T("torch が CPU 版なので、CUDA 版を入れ直します（2 GB ほど。数分）。", "torch is the CPU build. Reinstalling the CUDA build (about 2 GB; a few minutes)."));
                        Cmd(env + " && python -m pip install --upgrade --force-reinstall --no-deps --index-url https://download.pytorch.org/whl/cu128 torch torchvision", null, true);
                    }
                    Cmd(env + " && python -c \"import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '(no GPU: runs on the CPU)')\"", null, true);
                }

                Head(Program.T("5/5 デスクトップにアイコンを作る", "5/5 Creating the desktop icons"));
                if (o.NoShortcut) Log(Program.T("（アイコンは作りません）", "(no icons)"));
                else
                {
                    string app = "\"" + Path.Combine(o.Dir, "app.py") + "\"";
                    Must(env + " && python " + app + " --make-shortcut", Program.T("アイコンを作れませんでした。", "Could not create the icon."));
                    if (o.Lite) Must(env + " && python " + app + " --make-shortcut --edition lite", Program.T("Lite のアイコンを作れませんでした。", "Could not create the Lite icon."));
                }
                Log("");
                Log(o.NoShortcut ? Program.T("できました。", "Done.") : Program.T("できました。デスクトップの「TrapNote」から起動できます。", "Done. Start TrapNote from the desktop icon."));
                return true;
            }
            catch (Exception e)
            {
                Error = e.Message;
                Log("");
                Log(Program.T("失敗しました: ", "FAILED: ") + e.Message);
                Log(Program.T("記録: ", "log: ") + Path.Combine(o.Dir, "install.log"));
                return false;
            }
            finally
            {
                try { if (logFile != null) logFile.Close(); } catch (Exception) { }
                logFile = null;
            }
        }

        /// <summary>何も入れずに調べるだけ（/probe）: conda の場所、環境 trapnote の有無と Python、torch と GPU。インストールと同じ命令の組み立て（Cmd）を通す</summary>
        bool Probe()
        {
            Head("probe");
            bool exists;
            CondaRoot = FindConda(out exists);
            if (CondaRoot == "") { Log("conda: (none to use: a private Miniforge would be installed)"); return true; }
            Log("conda: " + CondaRoot);
            string act = "call \"" + Path.Combine(CondaRoot, "Scripts", "activate.bat") + "\" \"" + CondaRoot + "\"";
            Log("conda env list: env " + Program.EnvName + (EnvListed(CondaRoot, true) ? " listed" : " not listed") + ", by file: " + (exists ? "exists" : "not found"));
            if (!exists) return true;
            string env = act + " && conda activate " + Program.EnvName;
            // 足りないモジュールを足す命令を、入れずに流す（--dry-run）。何かを入れる・変えると出たら、本番でもそうなる。yml は、この exe の中のものを読む
            EnvSpec spec = ParseEnvYml(PayloadLines("env/trapnote-env.yml"));
            Log("yml: " + (spec.Ok ? "ok" : "(could not read)") + "   new env would be made by: " + CondaCreate(spec));
            if (spec.Ok)
            {
                string req = WriteRequirements(spec);
                Log("requirements: " + req);
                foreach (string line in File.ReadAllLines(req)) Log("    " + line);
                Log("pip dry-run: exit " + Cmd(env + " && python -m pip install --dry-run -r \"" + req + "\"", null, true));
            }
            List<string> outp = new List<string>();
            Cmd(env + " && python -c \"import sys;print(sys.executable)\"", outp, true);
            foreach (string line in outp) if (line.Trim().ToLowerInvariant().EndsWith("python.exe")) EnvPython = line.Trim();
            Log("python: " + EnvPython + (File.Exists(EnvPython) ? "" : "  (missing)"));
            Log("nvidia-smi: exit " + Cmd("where nvidia-smi", null, false));
            Log("torch cuda check: exit " + Cmd(env + " && python -c \"import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)\"", null, false));
            Cmd(env + " && python -c \"import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '(no GPU: runs on the CPU)')\"", null, true);
            Cmd(env + " && python -m pip --version", null, true);
            return true;
        }

        /// <summary>exe の中の payload.zip を o.Dir に広げる。data・miniforge3 など、zip にないものには触らない。ファイルの日時は zip のものにする（プログラムの日時 = build が、どの PC でも同じになる）</summary>
        int Extract()
        {
            int n = 0;
            string root = Path.GetFullPath(o.Dir).TrimEnd('\\') + "\\";
            using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            {
                if (s == null) throw new Exception("payload.zip is missing in this installer");
                using (ZipArchive zip = new ZipArchive(s, ZipArchiveMode.Read))
                {
                    foreach (ZipArchiveEntry e in zip.Entries)
                    {
                        if (e.FullName.EndsWith("/")) continue;
                        string dst = Path.GetFullPath(Path.Combine(o.Dir, e.FullName.Replace('/', '\\')));
                        if (!dst.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new Exception("bad path in payload: " + e.FullName);
                        Directory.CreateDirectory(Path.GetDirectoryName(dst));
                        using (Stream es = e.Open())
                        using (FileStream fs = new FileStream(dst, FileMode.Create, FileAccess.Write)) es.CopyTo(fs);
                        try { File.SetLastWriteTime(dst, e.LastWriteTime.LocalDateTime); } catch (Exception) { }
                        n++;
                    }
                }
            }
            return n;
        }

        /// <summary>使う conda の場所（使えるものがなければ ""。そのときは Miniforge を入れる）。envExists = そこに環境 trapnote がもうあるか。
        /// 使うのは、(1) 前にこのインストーラが入れた <フォルダ>\miniforge3、(2) 環境 trapnote がもうある conda（インストール.bat で入れた PC）。
        /// 環境のない conda（Anaconda など）には、新しく作らない（理由は、このファイルの先頭）</summary>
        string FindConda(out bool envExists)
        {
            envExists = false;
            string own = Path.Combine(o.Dir, "miniforge3");
            if (File.Exists(Path.Combine(own, "Scripts", "activate.bat")))
            {
                envExists = File.Exists(Path.Combine(own, "envs", Program.EnvName, "python.exe"));
                Log("  [use] " + own + (envExists ? "  (env " + Program.EnvName + " exists)" : "  (no env " + Program.EnvName + " yet)"));
                return own;
            }
            Log("  [-]   " + own);
            if (o.OwnConda) return "";
            string up = Environment.GetEnvironmentVariable("USERPROFILE") ?? "";
            string la = Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? "";
            List<string> c = new List<string>();
            foreach (string name in new string[] { "miniconda3", "anaconda3", "miniforge3", "mambaforge" })
            {
                if (up != "") c.Add(Path.Combine(up, name));
                if (la != "") c.Add(Path.Combine(la, name));
                c.Add(Path.Combine(@"C:\ProgramData", name));
            }
            c.Add(@"C:\trapnote\miniconda3");
            c.Add(@"C:\trapnote\miniforge3");
            c.Add(@"C:\areacheck\miniconda3");
            // 環境が conda の外（%USERPROFILE%\.conda\envs）にあることもある（conda のフォルダに書けない PC）。そのときは、conda に一覧を聞く
            bool outside = up != "" && File.Exists(Path.Combine(up, ".conda", "envs", Program.EnvName, "python.exe"));
            foreach (string r in c)
            {
                if (!File.Exists(Path.Combine(r, "Scripts", "activate.bat"))) { Log("  [-]   " + r); continue; }
                bool has = File.Exists(Path.Combine(r, "envs", Program.EnvName, "python.exe")) || (outside && EnvListed(r, false));
                Log(has ? "  [use] " + r + "  (env " + Program.EnvName + " exists)" : "  [skip] " + r + "  (a conda without the env " + Program.EnvName + ": not used)");
                if (has) { envExists = true; return r; }
            }
            return "";
        }

        /// <summary>conda env list に、環境 trapnote が名前つきで出るか</summary>
        bool EnvListed(string root, bool show)
        {
            List<string> envs = new List<string>();
            Cmd("call \"" + Path.Combine(root, "Scripts", "activate.bat") + "\" \"" + root + "\" && conda env list", envs, show);
            foreach (string line in envs)
            {
                string t = line.Trim();
                if (t.StartsWith(Program.EnvName + " ") || t.StartsWith(Program.EnvName + "\t")) return true;
            }
            return false;
        }

        /// <summary>この exe の中の payload.zip にあるテキストファイルの行（なければ空）</summary>
        static string[] PayloadLines(string name)
        {
            try
            {
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
                using (ZipArchive zip = new ZipArchive(s, ZipArchiveMode.Read))
                {
                    ZipArchiveEntry e = zip.GetEntry(name);
                    if (e != null) using (StreamReader r = new StreamReader(e.Open(), Encoding.UTF8)) return r.ReadToEnd().Replace("\r\n", "\n").Split('\n');
                }
            }
            catch (Exception) { }
            return new string[0];
        }

        /// <summary>env\trapnote-env.yml から読んだもの: conda の配布元、conda で入れるもの（python=3.11、pip）、pip で入れるもの</summary>
        class EnvSpec
        {
            public List<string> Channels = new List<string>(), Conda = new List<string>(), Pip = new List<string>();
            public bool Ok { get { return Conda.Count > 0 && Pip.Count > 0; } }
        }

        /// <summary>env\trapnote-env.yml（の行）を読む。この yml の形（channels と dependencies の並び。dependencies の中に「- pip:」の並び）だけを相手にする、簡単な読み方</summary>
        static EnvSpec ParseEnvYml(string[] lines)
        {
            EnvSpec s = new EnvSpec();
            string section = "";
            int pipIndent = -1;
            foreach (string raw in lines)
            {
                string t = raw.TrimStart();
                if (t == "" || t.StartsWith("#")) continue;
                int indent = raw.Length - t.Length;
                string body = Regex.Replace(t, @"\s+#.*$", "").Trim();                 // 行の後ろの説明（# …）を落とす
                if (!body.StartsWith("- "))
                {
                    if (indent == 0) { section = body == "channels:" ? "channels" : body == "dependencies:" ? "dependencies" : ""; pipIndent = -1; }
                    continue;
                }
                string item = body.Substring(2).Trim();
                if (item == "") continue;
                if (pipIndent >= 0 && indent > pipIndent) { s.Pip.Add(item); continue; }
                pipIndent = -1;
                if (section == "channels") { if (item != "defaults" && item != "nodefaults") s.Channels.Add(item); }
                else if (section == "dependencies")
                {
                    if (item == "pip:") pipIndent = indent;
                    else s.Conda.Add(item);
                }
            }
            if (s.Channels.Count == 0) s.Channels.Add("conda-forge");
            return s;
        }

        /// <summary>環境を作る conda の命令。配布元は yml に書いたもの（conda-forge）だけにする（--override-channels。利用者の .condarc の配布元を混ぜない）</summary>
        static string CondaCreate(EnvSpec s)
        {
            StringBuilder b = new StringBuilder("conda create -y -n " + Program.EnvName + " --override-channels");
            foreach (string c in s.Channels) b.Append(" -c " + c);
            foreach (string d in s.Conda) b.Append(" \"" + d + "\"");
            return b.ToString();
        }

        /// <summary>pip で入れるものを、requirements の形で <フォルダ>\install-requirements.txt に書く。返り値はそのパス</summary>
        string WriteRequirements(EnvSpec s)
        {
            string req = Path.Combine(o.Dir, "install-requirements.txt");
            File.WriteAllLines(req, s.Pip.ToArray(), new UTF8Encoding(false));
            return req;
        }

        /// <summary>フォルダのあるドライブの空き（GB）。分からなければ -1</summary>
        static double FreeGb(string dir)
        {
            try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(dir))).AvailableFreeSpace / 1073741824.0; }
            catch (Exception) { return -1; }
        }

        string InstallMiniforge()
        {
            string mf = Path.Combine(o.Dir, "miniforge3");
            if (o.DryRun)
            {
                Log("[dryrun] download " + Program.MiniforgeUrl);
                Log("[dryrun] run Miniforge3-Windows-x86_64.exe /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /NoRegistry=1 /NoShortcuts=1 /S /D=" + mf);
                return mf;
            }
            string tmp = Path.Combine(Path.GetTempPath(), "Miniforge3-Windows-x86_64.exe");
            Log(Program.T("Miniforge を取ってきます（約 90 MB）: ", "Downloading Miniforge (about 90 MB): ") + Program.MiniforgeUrl);
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;     // TLS 1.2（古い .NET の既定では GitHub につながらない）
            using (WebClient wc = new WebClient())
            {
                wc.Headers.Add("User-Agent", "TrapNote-Setup");
                wc.DownloadFile(Program.MiniforgeUrl, tmp);
            }
            long size = new FileInfo(tmp).Length;
            Log("  " + (size / 1048576) + " MB");
            if (size < 20 * 1048576) throw new Exception(Program.T("Miniforge を取ってこられませんでした（ファイルが小さすぎます）。", "The Miniforge download failed (file too small)."));
            Log(Program.T("Miniforge を入れます（1〜2 分。PATH やレジストリには書きません）: ", "Installing Miniforge (1-2 minutes; no PATH or registry changes): ") + mf);
            // /D= は最後に、引用符なしで付ける決まり（NSIS）。フォルダの名前に空白がないことは、先に確かめてある
            ProcessStartInfo psi = new ProcessStartInfo(tmp, "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /NoRegistry=1 /NoShortcuts=1 /S /D=" + mf);
            psi.UseShellExecute = false;
            using (Process p = Process.Start(psi))
            {
                p.WaitForExit();
                Log("  exit code " + p.ExitCode);
            }
            try { File.Delete(tmp); } catch (Exception) { }
            if (!File.Exists(Path.Combine(mf, "Scripts", "activate.bat"))) throw new Exception(Program.T("Miniforge を入れられませんでした。", "Miniforge was not installed."));
            return mf;
        }

        void Must(string cmdline, string failMessage)
        {
            if (o.DryRun) { Log("[dryrun] " + cmdline); return; }
            int code = Cmd(cmdline, null, true);
            if (code != 0) throw new Exception(failMessage + " (exit " + code + ")");
        }

        /// <summary>cmd.exe で 1 行を流す。出力は、capture があればそこへ、show なら記録へ。返り値は終了コード</summary>
        int Cmd(string cmdline, List<string> capture, bool show)
        {
            if (show) Log("> " + cmdline);
            ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", "/d /s /c \"" + cmdline + "\"");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.WorkingDirectory = o.Dir;
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
            psi.EnvironmentVariables["PIP_NO_INPUT"] = "1";
            psi.EnvironmentVariables["CONDA_ALWAYS_YES"] = "true";
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";             // 利用者のフォルダに入っている別の Python のモジュールを拾わない
            using (Process p = new Process())
            {
                p.StartInfo = psi;
                DataReceivedEventHandler h = delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data == null) return;
                    string line = e.Data.TrimEnd();
                    if (capture != null) lock (capture) capture.Add(line);
                    if (show && line != "") Log("  " + line);
                };
                p.OutputDataReceived += h;
                p.ErrorDataReceived += h;
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                p.WaitForExit();
                return p.ExitCode;
            }
        }
    }

    class MainForm : Form
    {
        readonly Options o;
        TextBox txtDir, txtLog;
        CheckBox chkLite, chkOwn;
        Button btnBrowse, btnInstall, btnClose, btnStart;
        ProgressBar bar;
        Label lblState;
        BackgroundWorker worker;
        bool running;

        public MainForm(Options o)
        {
            this.o = o;
            string ver = Program.Version();
            Text = "TrapNote " + Program.T("セットアップ", "Setup") + (ver != "" ? "  (" + ver + ")" : "");
            Font = new Font(Program.Ja ? "Meiryo UI" : "Segoe UI", 9F);
            ClientSize = new Size(680, 560);
            MinimumSize = new Size(560, 460);
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }

            Label intro = new Label();
            intro.SetBounds(16, 12, 648, 96);
            intro.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            intro.Text = Program.T(
                "TrapNote（カメラトラップの動画を管理するアプリ）を、この PC に入れます。\r\n" +
                "・インターネットにつながっている必要があります。Python の環境を作るので、10〜30 分かかり、ディスクを 10 GB ほど使います。\r\n" +
                "・前に入れたフォルダを指定すると、プログラムだけを新しくします（設定・モデル・ログは残ります）。\r\n" +
                "・TrapNote を起動しているときは、先に終了してください。",
                "This installs TrapNote (an app for managing camera-trap videos) on this PC.\r\n" +
                "- An internet connection is needed. Creating the Python environment takes 10-30 minutes and about 10 GB of disk.\r\n" +
                "- If you choose the folder of an earlier installation, only the program is replaced (settings, models and logs are kept).\r\n" +
                "- Quit TrapNote first if it is running.");
            Controls.Add(intro);

            Label l1 = new Label();
            l1.SetBounds(16, 116, 120, 22);
            l1.Text = Program.T("入れるフォルダ", "Install folder");
            Controls.Add(l1);
            txtDir = new TextBox();
            txtDir.SetBounds(140, 113, 430, 24);
            txtDir.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            txtDir.Text = o.Dir;
            Controls.Add(txtDir);
            btnBrowse = new Button();
            btnBrowse.SetBounds(578, 111, 86, 27);
            btnBrowse.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnBrowse.Text = Program.T("選ぶ…", "Browse…");
            btnBrowse.Click += delegate
            {
                using (FolderBrowserDialog d = new FolderBrowserDialog())
                {
                    d.Description = Program.T("TrapNote を入れるフォルダ（半角の英数字だけの名前にしてください）", "Folder for TrapNote (ASCII letters and digits only)");
                    if (d.ShowDialog(this) == DialogResult.OK)
                    {
                        string p = d.SelectedPath;
                        if (!p.ToLowerInvariant().TrimEnd('\\').EndsWith("trapnote")) p = Path.Combine(p, "TrapNote");
                        txtDir.Text = p;
                    }
                }
            };
            Controls.Add(btnBrowse);

            chkLite = new CheckBox();
            chkLite.SetBounds(140, 144, 520, 22);
            chkLite.Text = Program.T("TrapNote Lite（検出と目視の確認まで）のアイコンも作る", "Also create an icon for TrapNote Lite (detection and manual verification only)");
            chkLite.Checked = o.Lite;
            Controls.Add(chkLite);
            chkOwn = new CheckBox();
            chkOwn.SetBounds(140, 168, 520, 22);
            chkOwn.Text = Program.T("前に作った Python の環境（trapnote）があっても使わず、専用の Miniforge に作る", "Do not reuse an existing Python environment (trapnote); create it in a private Miniforge");
            chkOwn.Checked = o.OwnConda;
            Controls.Add(chkOwn);

            lblState = new Label();
            lblState.SetBounds(16, 198, 648, 20);
            lblState.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(lblState);
            bar = new ProgressBar();
            bar.SetBounds(16, 220, 648, 10);
            bar.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            bar.Style = ProgressBarStyle.Blocks;
            Controls.Add(bar);

            txtLog = new TextBox();
            txtLog.SetBounds(16, 238, 648, 268);
            txtLog.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            txtLog.Multiline = true;
            txtLog.ReadOnly = true;
            txtLog.ScrollBars = ScrollBars.Vertical;
            txtLog.WordWrap = false;
            txtLog.Font = new Font("Consolas", 8.5F);
            txtLog.BackColor = Color.White;
            Controls.Add(txtLog);

            btnInstall = new Button();
            btnInstall.SetBounds(368, 518, 110, 30);
            btnInstall.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
            btnInstall.Text = Program.T("インストール", "Install");
            btnInstall.Click += delegate { StartInstall(); };
            Controls.Add(btnInstall);
            btnStart = new Button();
            btnStart.SetBounds(484, 518, 96, 30);
            btnStart.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
            btnStart.Text = Program.T("起動する", "Start");
            btnStart.Enabled = false;
            btnStart.Click += delegate { StartApp(); };
            Controls.Add(btnStart);
            btnClose = new Button();
            btnClose.SetBounds(586, 518, 78, 30);
            btnClose.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
            btnClose.Text = Program.T("閉じる", "Close");
            btnClose.Click += delegate { Close(); };
            Controls.Add(btnClose);
            AcceptButton = btnInstall;

            FormClosing += delegate(object s, FormClosingEventArgs e)
            {
                if (!running) return;
                e.Cancel = MessageBox.Show(this, Program.T("インストールの途中です。閉じると、途中で止まります。閉じますか？", "The installation is still running. Closing stops it. Close anyway?"),
                                           Text, MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes;
            };
        }

        void Append(string s)
        {
            if (IsDisposed) return;
            if (InvokeRequired) { try { BeginInvoke(new Action<string>(Append), s); } catch (Exception) { } return; }
            if (s.StartsWith("=== ")) lblState.Text = s.Trim('=', ' ');
            txtLog.AppendText(s + "\r\n");
        }

        void StartInstall()
        {
            string dir = txtDir.Text.Trim().TrimEnd('\\');
            string why = Installer.CheckDir(dir);
            if (why != null) { MessageBox.Show(this, why, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            o.Dir = dir;
            o.Lite = chkLite.Checked;
            o.OwnConda = chkOwn.Checked;
            bool update = File.Exists(Path.Combine(dir, "app.py"));
            if (update && MessageBox.Show(this, Program.T("このフォルダには、もう TrapNote が入っています。プログラムを新しくします（設定・モデル・ログは残ります）。続けますか？",
                                                        "TrapNote is already in this folder. The program will be replaced (settings, models and logs are kept). Continue?"),
                                          Text, MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK) return;
            running = true;
            btnInstall.Enabled = btnBrowse.Enabled = txtDir.Enabled = chkLite.Enabled = chkOwn.Enabled = false;
            btnStart.Enabled = false;
            bar.Style = ProgressBarStyle.Marquee;
            Installer inst = new Installer(o, Append);
            worker = new BackgroundWorker();
            worker.DoWork += delegate(object s, DoWorkEventArgs e) { e.Result = inst.Run(); };
            worker.RunWorkerCompleted += delegate(object s, RunWorkerCompletedEventArgs e)
            {
                running = false;
                bar.Style = ProgressBarStyle.Blocks;
                bool ok = e.Error == null && (bool)e.Result;
                bar.Value = ok ? 100 : 0;
                btnInstall.Enabled = btnBrowse.Enabled = txtDir.Enabled = chkLite.Enabled = chkOwn.Enabled = true;
                btnStart.Enabled = ok && inst.EnvPython != "";
                envPython = inst.EnvPython;
                if (ok)
                {
                    lblState.Text = Program.T("できました", "Done");
                    MessageBox.Show(this, Program.T("インストールが終わりました。デスクトップの「TrapNote」から起動できます。", "The installation is complete. Start TrapNote from the desktop icon."),
                                    Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                else
                {
                    lblState.Text = Program.T("失敗しました", "Failed");
                    MessageBox.Show(this, Program.T("インストールに失敗しました。\r\n", "The installation failed.\r\n") + inst.Error + "\r\n\r\n" +
                                          Program.T("記録: ", "Log: ") + Path.Combine(o.Dir, "install.log"), Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            };
            worker.RunWorkerAsync();
        }

        string envPython = "";

        void StartApp()
        {
            try
            {
                string pyw = Path.Combine(Path.GetDirectoryName(envPython), "pythonw.exe");
                if (!File.Exists(pyw)) pyw = envPython;
                ProcessStartInfo psi = new ProcessStartInfo(pyw, "\"" + Path.Combine(o.Dir, "app.py") + "\"");
                psi.WorkingDirectory = o.Dir;
                psi.UseShellExecute = false;
                Process.Start(psi);
            }
            catch (Exception e) { MessageBox.Show(this, e.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }
    }
}
