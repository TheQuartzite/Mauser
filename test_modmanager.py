"""Tests. Run with:  python3 test_modmanager.py

Everything runs against a throwaway fake game folder; nothing touches a real install.
"""
import os
import shutil
import struct
import sys
import tempfile
import zipfile

SEVEN_ZIP_TESTED = False

import archives
import core
import fomod
import loadorder
import steam


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def read(path):
    with open(path, "rb") as f:
        return f.read()


def plugin_bytes(masters=(), flags=0):
    body = b"HEDR" + struct.pack("<HfII", 12, 1.0, 0, 0x800)
    for m in masters:
        raw = m.encode() + b"\0"
        body += b"MAST" + struct.pack("<H", len(raw)) + raw + b"DATA" + struct.pack("<HQ", 8, 0)
    return b"TES4" + struct.pack("<IIIIHH", len(body), flags, 0, 0, 131, 0) + body


def make_game(root):
    game = os.path.join(root, "steamapps", "common", "Fallout 4")
    write(os.path.join(game, "Fallout4.exe"))
    write(os.path.join(game, "Fallout4Launcher.exe"), b"launcher")
    write(os.path.join(game, "Data", "Fallout4.esm"), plugin_bytes(flags=1))
    write(os.path.join(game, "Data", "Fallout4 - Meshes.ba2"))
    write(os.path.join(game, "Data", "Video", "intro.bk2"), b"vanilla video")
    write(os.path.join(game, "Fallout4.ccc"), "ccBGSFO4001-PipBoy(Black).esl\r\n")
    user = os.path.join(root, "steamapps", "compatdata", "377160", "pfx", "drive_c", "users", "steamuser")
    os.makedirs(os.path.join(user, "AppData", "Local", "Fallout4"))
    os.makedirs(os.path.join(user, "Documents", "My Games", "Fallout4"))
    return game


def tree(root, files):
    for rel, data in files.items():
        write(os.path.join(root, *rel.split("/")), data)
    return root


def test_steam(tmp):
    game = make_game(tmp)
    assert steam.looks_like_game(game) and not steam.looks_like_game(tmp)
    assert steam.normalize_game_dir(os.path.join(game, "Data")) == game
    p = steam.GamePaths(game)
    assert p.effective_prefix().endswith(os.path.join("377160", "pfx"))
    assert p.plugins_txt().endswith(os.path.join("Local", "Fallout4", "Plugins.txt"))
    assert p.custom_ini().endswith("Fallout4Custom.ini") and not p.has_f4se()
    # prefix given as the compatdata folder is accepted too
    assert steam.GamePaths(game, os.path.dirname(p.effective_prefix())).effective_prefix() == p.effective_prefix()

    ini = p.custom_ini()
    assert steam.enable_loose_files(ini) and not steam.enable_loose_files(ini)
    assert read(ini) == b"[Archive]\r\nbInvalidateOlderFiles=1\r\nsResourceDataDirsFinal=\r\n"
    text = "[Display]\niSize W=1920\n\n[Archive]\nbInvalidateOlderFiles=0\nsOther=1\n[General]\nx=1\n"
    out = steam.with_loose_files(text)
    assert out == ("[Display]\niSize W=1920\n\n[Archive]\nbInvalidateOlderFiles=1\nsOther=1\n"
                   "sResourceDataDirsFinal=\n[General]\nx=1\n"), out
    assert steam.loose_files_enabled(out) and not steam.loose_files_enabled(text)
    with open(ini, "w") as f:
        f.write(text)
    assert steam.enable_loose_files(ini) and os.path.exists(ini + ".modifikationsmann-backup")
    assert steam.loose_files_enabled(read(ini).decode()) and b"iSize W=1920" in read(ini)


def test_archives(tmp):
    z = os.path.join(tmp, "plain.zip")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Cool Mod v1/Cool.esp", plugin_bytes())
        f.writestr("Cool Mod v1/Meshes/a.nif", "mesh")
        f.writestr("__MACOSX/junk", "j")
    out = os.path.join(tmp, "plain")
    archives.extract(z, out)
    lay = archives.find_layout(out)
    assert lay.kind == "data" and os.path.basename(lay.base) == "Cool Mod v1"

    bad = os.path.join(tmp, "bad.zip")
    with zipfile.ZipFile(bad, "w") as f:
        f.writestr("../escape.txt", "no")
    try:
        archives.extract(bad, os.path.join(tmp, "bad"))
        raise AssertionError("unsafe archive was unpacked")
    except archives.ArchiveError:
        pass
    assert not os.path.exists(os.path.join(tmp, "escape.txt"))

    tool = archives.find_7zip()
    if tool:
        import subprocess
        src = tree(os.path.join(tmp, "s7"), {"mod/Data/Textures/t.dds": "tex", "mod/readme.txt": "hi"})
        a = os.path.join(tmp, "m.7z")
        subprocess.run([tool, "a", "-bd", a, "mod"], cwd=src, check=True, stdout=subprocess.DEVNULL)
        out7 = os.path.join(tmp, "o7")
        archives.extract(a, out7)
        lay = archives.find_layout(out7)
        assert lay.kind == "data" and os.path.basename(lay.base) == "Data"
        assert read(os.path.join(lay.base, "Textures", "t.dds")) == b"tex"
        os.symlink("/etc/passwd", os.path.join(src, "mod", "link"))
        linked = os.path.join(tmp, "links.7z")
        subprocess.run([tool, "a", "-bd", "-snl", linked, "mod"], cwd=src, check=True, stdout=subprocess.DEVNULL)
        try:
            archives.extract(linked, os.path.join(tmp, "o7l"))
            raise AssertionError("archive with links was unpacked")
        except archives.ArchiveError:
            pass
        broken = os.path.join(tmp, "broken.7z")
        write(broken, b"7z\xbc\xaf\x27\x1c" + b"\0" * 64)
        try:
            archives.extract(broken, os.path.join(tmp, "o7b"))
            raise AssertionError("broken archive was unpacked")
        except archives.ArchiveError:
            pass
        global SEVEN_ZIP_TESTED
        SEVEN_ZIP_TESTED = True

    f4se = tree(os.path.join(tmp, "f4se"), {"f4se_0_06_23/f4se_loader.exe": "exe", "f4se_0_06_23/f4se_1_10_163.dll": "dll",
                                             "f4se_0_06_23/Data/Scripts/Actor.pex": "pex"})
    lay = archives.find_layout(f4se)
    assert lay.kind == "root" and os.path.basename(lay.base) == "f4se_0_06_23"
    assert archives.find_layout(tree(os.path.join(tmp, "enb"), {"d3d11.dll": "d"})).kind == "root"
    assert archives.find_layout(tree(os.path.join(tmp, "odd"), {"notes/readme.txt": "d", "x.txt": "y"})).kind == "unknown"
    assert archives.find_layout(tree(os.path.join(tmp, "fo"), {"w/fomod/ModuleConfig.xml": "<config/>",
                                                               "w/opt/a.esp": "a"})).kind == "fomod"
    try:
        archives.extract(os.path.join(tmp, "plain", "Cool Mod v1", "Cool.esp"), os.path.join(tmp, "nope"))
        raise AssertionError("non-archive accepted")
    except archives.ArchiveError:
        pass

    dst = tree(os.path.join(tmp, "merge"), {"Meshes/old.nif": "old"})
    n = archives.copy_tree(tree(os.path.join(tmp, "msrc"), {"meshes/new.nif": "new", "MESHES/x/y.nif": "y"}), dst)
    assert n == 2 and sorted(os.listdir(dst)) == ["Meshes"] and sorted(os.listdir(os.path.join(dst, "Meshes"))) == \
        ["new.nif", "old.nif", "x"]


FOMOD_XML = """<?xml version="1.0" encoding="UTF-16"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <moduleName>Test Installer</moduleName>
  <requiredInstallFiles><folder source="Core" destination=""/></requiredInstallFiles>
  <installSteps order="Explicit">
    <installStep name="Version">
      <optionalFileGroups order="Explicit">
        <group name="Pick one" type="SelectExactlyOne">
          <plugins order="Explicit">
            <plugin name="Vanilla">
              <description>Plain</description>
              <files><file source="Options\\vanilla.esp" destination="Main.esp"/></files>
              <typeDescriptor><type name="Recommended"/></typeDescriptor>
            </plugin>
            <plugin name="Fancy">
              <description>More</description>
              <conditionFlags><flag name="fancy">On</flag></conditionFlags>
              <files><file source="options/FANCY.esp" destination="Main.esp" priority="1"/></files>
              <typeDescriptor><type name="Optional"/></typeDescriptor>
            </plugin>
          </plugins>
        </group>
        <group name="Extras" type="SelectAny">
          <plugins>
            <plugin name="Zeta"><description/><files><folder source="Extras/Z" destination="Textures"/></files>
              <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
            <plugin name="Alpha"><description/><files><folder source="Extras/A"/></files>
              <typeDescriptor><dependencyType><defaultType name="Optional"/><patterns><pattern>
                <dependencies><fileDependency file="DLCCoast.esm" state="Active"/></dependencies>
                <type name="Required"/></pattern></patterns></dependencyType></typeDescriptor></plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
    <installStep name="Fancy settings">
      <visible><flagDependency flag="fancy" value="On"/></visible>
      <optionalFileGroups>
        <group name="Glow" type="SelectAtMostOne">
          <plugins order="Explicit">
            <plugin name="Glow"><description/><conditionFlags><flag name="glow">Yes</flag></conditionFlags>
              <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
  <conditionalFileInstalls><patterns>
    <pattern><dependencies operator="And"><flagDependency flag="fancy" value="On"/>
        <flagDependency flag="glow" value="Yes"/></dependencies>
      <files><file source="Glow/glow.dds" destination="Textures/glow.dds"/></files></pattern>
    <pattern><dependencies operator="Or"><flagDependency flag="nope" value="1"/>
        <fileDependency file="Missing.esp" state="Missing"/></dependencies>
      <files><file source="Compat/patch.esp"/></files></pattern>
  </patterns></conditionalFileInstalls>
</config>
"""


def test_fomod(tmp):
    fm = fomod.parse(FOMOD_XML.encode("utf-16"))
    assert fm.name == "Test Installer" and [s.name for s in fm.steps] == ["Version", "Fancy settings"]
    extras = fm.steps[0].groups[1]
    assert [o.name for o in extras.options] == ["Alpha", "Zeta"]          # default order is alphabetical
    base = tree(os.path.join(tmp, "fm"), {
        "core/readme.txt": "core", "Options/Vanilla.esp": "vanilla", "Options/Fancy.esp": "fancy",
        "Extras/Z/z.dds": "z", "Extras/A/Meshes/a.nif": "a", "Glow/glow.dds": "glow", "Compat/patch.esp": "patch"})

    s = fomod.Session(fm)
    assert s.selection(0, 0) == {0} and s.selection(0, 1) == set()
    assert not s.step_visible(1) and s.next_visible(0) is None
    dest = os.path.join(tmp, "fm_out1")
    count, missing = fomod.install(base, s.result(), dest)
    assert not missing and read(os.path.join(dest, "Main.esp")) == b"vanilla"
    assert sorted(os.listdir(dest)) == ["Compat", "Main.esp", "readme.txt"], os.listdir(dest)

    s = fomod.Session(fm, lambda name: "Active" if name == "DLCCoast.esm" else "Missing")
    assert s.selection(0, 1) == {0}                                        # Alpha becomes required
    assert fomod.option_type(extras.options[0], {}, s.file_state) == "Required"
    s.choices[(0, 0)] = {1}
    s.choices[(0, 1)] = {0, 1}
    assert s.step_visible(1) and s.next_visible(0) == 1 and s.next_visible(1, -1) == 0
    assert s.flags_before(1) == {"fancy": "On"}
    s.choices[(1, 0)] = {0}
    dest = os.path.join(tmp, "fm_out2")
    count, missing = fomod.install(base, s.result(), dest)
    assert not missing and read(os.path.join(dest, "Main.esp")) == b"fancy"
    assert read(os.path.join(dest, "Textures", "glow.dds")) == b"glow" and os.path.exists(os.path.join(dest, "Textures", "z.dds"))
    assert os.path.exists(os.path.join(dest, "Extras", "A", "Meshes", "a.nif"))

    g = fm.steps[0].groups[0]
    assert fomod.valid_selection(g, {0}) and not fomod.valid_selection(g, set()) and not fomod.valid_selection(g, {0, 1})
    assert fomod.valid_selection(fm.steps[1].groups[0], set())
    _, missing = fomod.install(base, [fomod.FileItem("nope/x.esp", "x.esp", False), fomod.FileItem("../up", "", True)], dest)
    assert missing == ["nope/x.esp", "../up"]
    try:
        fomod.parse(b"<config><unclosed>")
        raise AssertionError("bad xml parsed")
    except fomod.FomodError:
        pass


def test_core(tmp):
    game = make_game(tmp)
    data = os.path.join(game, "Data")
    mgr = core.Manager(os.path.join(tmp, "library"))
    mgr.set_game(os.path.join(game, "Data"))
    assert mgr.game_dir == game and mgr.link_method_hint() == "hard links"

    a = mgr.add_mod("Mod A", tree(os.path.join(tmp, "a"), {
        "A.esp": plugin_bytes(["Fallout4.esm"]), "meshes/Weapons/gun.nif": "A gun", "textures/a.dds": "A tex",
        "VIDEO/intro.bk2": "A video"}), source="a.zip")
    b = mgr.add_mod("Mod B", tree(os.path.join(tmp, "b"), {
        "B.esp": plugin_bytes(["A.esp"]), "Meshes/weapons/GUN.NIF": "B gun", "Meshes/b.nif": "B mesh"}))
    assert a.id == "mod-a" and a.files == 4 and mgr.mod_plugins(a) == ["A.esp"]
    rep = mgr.deploy()
    assert not rep.errors and rep.linked == 6 and rep.backed_up == 1 and rep.methods == {"hard": 6}, rep
    # one folder per name regardless of case; the game's own "Video" folder is reused
    assert sorted(e.lower() for e in os.listdir(data)) == ["a.esp", "b.esp", "fallout4 - meshes.ba2", "fallout4.esm",
                                                           "meshes", "textures", "video"], os.listdir(data)
    assert "Video" in os.listdir(data)
    gun = steam.descend(data, "meshes", "weapons", "gun.nif")
    assert len(os.listdir(os.path.dirname(gun))) == 1 and len(os.listdir(steam.descend(data, "meshes"))) == 2
    assert read(gun) == b"B gun"                                           # later mod wins
    assert read(os.path.join(data, "Video", "intro.bk2")) == b"A video"
    conf = mgr.conflicts()
    assert conf[b.id]["wins"] == ["data/meshes/weapons/gun.nif"] and conf[a.id]["loses"] == ["data/meshes/weapons/gun.nif"]
    rep = mgr.deploy()
    assert rep.linked == 0 and rep.kept == 6 and rep.removed == 0

    mgr.move(1, 0)                                      # B first, so A now wins
    rep = mgr.deploy()
    assert read(steam.descend(data, "meshes", "weapons", "gun.nif")) == b"A gun" and rep.linked == 1 and not rep.errors

    mgr.set_enabled(a.id, False)
    rep = mgr.deploy()
    assert not os.path.exists(os.path.join(data, "A.esp")) and not os.path.exists(os.path.join(data, "textures"))
    assert read(steam.descend(data, "meshes", "weapons", "gun.nif")) == b"B gun"
    assert read(os.path.join(data, "Video", "intro.bk2")) == b"vanilla video" and rep.restored == 1

    # a deployed file rewritten by something else is kept, not deleted
    target = steam.descend(data, "meshes", "b.nif")
    os.unlink(target)
    write(target, b"edited by the user")
    rep = mgr.remove_mod(b.id)
    assert read(steam.descend(mgr.backup_dir, "changed", "Data", "meshes", "b.nif")) == b"edited by the user"
    assert sorted(os.listdir(data)) == ["Fallout4 - Meshes.ba2", "Fallout4.esm", "Video"] and not rep.errors
    assert not os.path.isdir(os.path.join(mgr.mods_dir, "mod-b")) and mgr.deployed_count() == 0

    # root mods, purge, and state reload
    f = mgr.add_mod("F4SE", tree(os.path.join(tmp, "f"), {"f4se_loader.exe": "loader", "Data/Scripts/Actor.pex": "pex",
                                                          "Fallout4Launcher.exe": "replaced"}), target="root")
    mgr.set_enabled(a.id, True)
    rep = mgr.deploy()
    assert not rep.errors and steam.GamePaths(game).has_f4se() and read(os.path.join(game, "Fallout4Launcher.exe")) == b"replaced"
    assert read(os.path.join(data, "Scripts", "Actor.pex")) == b"pex" and mgr.mod_plugins(f) == []
    again = core.Manager(os.path.join(tmp, "library"))
    assert [m.name for m in again.mods] == ["Mod A", "F4SE"] and again.game_dir == game and again.mods[1].target == "root"
    rep = again.purge()
    assert not rep.errors and read(os.path.join(game, "Fallout4Launcher.exe")) == b"launcher"
    assert sorted(os.listdir(game)) == ["Data", "Fallout4.ccc", "Fallout4.exe", "Fallout4Launcher.exe"]
    assert sorted(os.listdir(data)) == ["Fallout4 - Meshes.ba2", "Fallout4.esm", "Video"]
    assert read(os.path.join(data, "Video", "intro.bk2")) == b"vanilla video"
    assert read(os.path.join(mgr.mod_dir(a), "textures", "a.dds")) == b"A tex"       # library untouched

    # other link modes
    for mode, method in (("symlink", "sym"), ("copy", "copy")):
        again.link_mode = mode
        rep = again.deploy()
        assert not rep.errors and set(rep.methods) == {method}, rep
        assert read(os.path.join(data, "textures", "a.dds")) == b"A tex"
        assert os.path.islink(os.path.join(data, "A.esp")) == (mode == "symlink")
        assert not again.purge().errors and sorted(os.listdir(data)) == ["Fallout4 - Meshes.ba2", "Fallout4.esm", "Video"]

    # moving to another game folder cleans the first one
    again.link_mode = "auto"
    again.deploy()
    other = make_game(os.path.join(tmp, "second"))
    again.set_game(other)
    assert sorted(os.listdir(data)) == ["Fallout4 - Meshes.ba2", "Fallout4.esm", "Video"]
    assert not again.deploy().errors and os.path.exists(os.path.join(other, "Data", "A.esp"))
    assert core.Manager(os.path.join(tmp, "empty")).deploy().errors


def test_loadorder(tmp):
    game = make_game(os.path.join(tmp, "lo"))
    data = os.path.join(game, "Data")
    write(os.path.join(data, "Patch.esp"), plugin_bytes(["Fallout4.esm", "Base.esm", "Light.esp"]))
    write(os.path.join(data, "Base.esm"), plugin_bytes(["Fallout4.esm"], flags=1))
    write(os.path.join(data, "Light.esp"), plugin_bytes(["Fallout4.esm"], flags=0x200))
    write(os.path.join(data, "Orphan.esp"), plugin_bytes(["Gone.esm"]))
    write(os.path.join(data, "ccBGSFO4001-PipBoy(Black).esl"), plugin_bytes(flags=0x201))
    write(os.path.join(data, "Broken.esp"), b"junk")
    txt = steam.GamePaths(game).plugins_txt()
    write(txt, b"# header\r\n*Patch.esp\r\nRemoved.esp\r\n*light.esp\r\n*Fallout4.esm\r\n")
    lo = loadorder.LoadOrder(data, txt, game)
    assert [(e.name, e.enabled) for e in lo.entries] == [("Patch.esp", True), ("Light.esp", True), ("Base.esm", False),
                                                         ("Broken.esp", False), ("Orphan.esp", False)], lo.entries
    info = {e.name: e for e in lo.entries}
    assert info["Base.esm"].is_master and info["Light.esp"].is_light and not info["Patch.esp"].is_master
    assert info["Patch.esp"].info.masters == ["Fallout4.esm", "Base.esm", "Light.esp"] and not info["Broken.esp"].info.readable
    assert lo.problems() == {"Patch.esp": ["needs Base.esm, which is missing or disabled",
                                           "needs Light.esp, which loads after it"]}
    lo.reload(enable_new={"base.esm", "Orphan.esp"})
    assert {e.name for e in lo.entries if e.enabled} == {"Patch.esp", "Light.esp", "Base.esm", "Orphan.esp"}
    lo.sort()
    assert [e.name for e in lo.entries] == ["Base.esm", "Light.esp", "Patch.esp", "Broken.esp", "Orphan.esp"]
    assert lo.problems() == {"Orphan.esp": ["needs Gone.esm, which is missing or disabled"]}
    lo.save()
    assert read(txt) == b"# header\r\n*Base.esm\r\n*Light.esp\r\n*Patch.esp\r\nBroken.esp\r\n*Orphan.esp\r\n"
    assert read(txt + ".modifikationsmann-backup").startswith(b"# header\r\n*Patch.esp")
    lo.move(4, 0)
    assert lo.entries[0].name == "Orphan.esp"
    fresh = loadorder.LoadOrder(os.path.join(tmp, "nowhere"), os.path.join(tmp, "nowhere", "Plugins.txt"))
    assert fresh.entries == []


if __name__ == "__main__":
    for name, fn in (("steam paths and ini", test_steam), ("archives", test_archives), ("guided installers", test_fomod),
                     ("deployment", test_core), ("load order", test_loadorder)):
        tmp = tempfile.mkdtemp(prefix="modmann_test_")
        try:
            fn(tmp)
            print("%-20s ok" % name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("7-Zip unpacking: %s" % ("tested" if SEVEN_ZIP_TESTED else "skipped (no 7-Zip found)"))
    sys.exit(0)
