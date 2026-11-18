param([string]$DllPath, [string]$MonoDir, [string]$ManagedDir, [string]$TypeName,
      [string]$Methods = "Title,Load,Long,Repeat")
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$k32 = @"
using System;
using System.Runtime.InteropServices;
public static class K32 {
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr LoadLibrary(string path);
}
"@
Add-Type -TypeDefinition $k32
$monoDll = Join-Path $MonoDir "EmbedRuntime\mono-2.0-bdwgc.dll"
$h = [K32]::LoadLibrary($monoDll)
if ($h -eq [IntPtr]::Zero) { Write-Output ("E: could not load " + $monoDll); exit 1 }

$src = @"
using System;
using System.Runtime.InteropServices;
public static class MonoHost {
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern void mono_set_dirs(string lib, string etc);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_jit_init(string name);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_domain_assembly_open(IntPtr domain, string name);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_assembly_get_image(IntPtr assembly);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_class_from_name(IntPtr image, string ns, string name);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_class_get_method_from_name(IntPtr klass, string name, int argc);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_runtime_invoke(IntPtr method, IntPtr obj, IntPtr[] parms, IntPtr exc);
    [DllImport(\"mono-2.0-bdwgc.dll\", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr mono_string_to_utf8(IntPtr str);
}
"@
$src = $src.Replace('\"', '"')
Add-Type -TypeDefinition $src -Language CSharp

$env:MONO_PATH = $ManagedDir
[System.IO.Directory]::SetCurrentDirectory($MonoDir)

$domain = [MonoHost]::mono_jit_init("oracle")
if ($domain -eq [IntPtr]::Zero) { Write-Output "E: jit_init failed"; exit 1 }
$asm = [MonoHost]::mono_domain_assembly_open($domain, $DllPath)
if ($asm -eq [IntPtr]::Zero) { Write-Output "E: assembly open failed"; exit 1 }
$image = [MonoHost]::mono_assembly_get_image($asm)
$klass = [MonoHost]::mono_class_from_name($image, "", $TypeName)
if ($klass -eq [IntPtr]::Zero) { Write-Output "E: class not found"; exit 1 }
foreach ($m in $Methods.Split(",")) {
    $mm = [MonoHost]::mono_class_get_method_from_name($klass, $m, 0)
    if ($mm -eq [IntPtr]::Zero) { Write-Output ($m + "=<missing>"); continue }
    $rs = [MonoHost]::mono_runtime_invoke($mm, [IntPtr]::Zero, $null, [IntPtr]::Zero)
    $u8 = [MonoHost]::mono_string_to_utf8($rs)
    $s = [System.Runtime.InteropServices.Marshal]::PtrToStringAnsi($u8)
    Write-Output ($m + "=" + $s)
}
