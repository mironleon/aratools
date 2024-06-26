import csv
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from fastkml import kml

from aratools.coordinate import WKT_converter
from aratools.latex import etappe_to_pdf


@contextmanager
def csvreader(path: str | Path) -> Iterator[list[str]]:
    file_obj = open(Path(path), mode="r", newline="")
    reader = csv.reader(file_obj)
    try:
        yield reader  # type: ignore
    finally:
        file_obj.close()


class Parcour:
    """
    Object representing adventure race parcour, built from a list of participating teams
    and a KML file representing etappes with checkpoints
    """

    def __init__(self, team_csv_path: str | Path, etappe_kml_path: str | Path):
        self._process_teams_file(team_csv_path)
        self._process_etappes_kml(etappe_kml_path)

    def _process_teams_file(self, path: str | Path):
        # team names, single csv file, names only
        path = Path(path).resolve()
        with open(path, 'r') as f:
            lines = f.readlines()
        lines = [line for line in lines if len(line)]
        self.team_names = tuple(f"{i+1} {line.strip()}" for i, line in enumerate(lines))
        assert len(self.team_names)

    def _process_etappes_kml(self, path: str | Path):
        path = Path(path).resolve()
        assert path.suffix == ".kml"
        with open(path, "rb") as f:
            kml_str = f.read()
        k = kml.KML()
        k.from_string(kml_str)
        doc = list(k.features())[0]
        assert isinstance(doc, kml.Document)
        folders: list[kml.Folder] = list(doc.features())  # type: ignore
        assert all(isinstance(f, kml.Folder) for f in folders)
        # name should be 'Etappe_4_fietsen' format, so containing two underscores
        _etappes = tuple(
            Etappe.from_kml_folder(f) for f in folders if str(f.name).count("_") == 2
        )
        self.etappes = {et.idx: et for et in _etappes}

    def generate_ponskaart_pdfs(self, path: str | Path):
        """
        Generate a 1 a3 landscape pdf file per etappe, containing
        ponskaarts for all the teams
        """
        path = Path(path)
        for idx, etappe in self.etappes.items():
            etappe_to_pdf(etappe, self.team_names, path / f"Etappe_{idx}")


@dataclass(frozen=True)
class CheckPoint:
    idx: int
    score: int  # how many points scoring the cp is worth
    hint: str
    hidden: bool
    coordinate: tuple[
        int, int
    ]  # https://nl.wikipedia.org/wiki/Rijksdriehoeksco%C3%B6rdinaten

    def __lt__(self, other):
        return self.idx < other

    def __gt__(self, other):
        return self.idx > other


@dataclass(frozen=True)
class Etappe(Collection[CheckPoint]):
    idx: int
    kind: str  # fietsen, kano, run-bike
    checkpoints: tuple[CheckPoint, ...]

    def __post_init__(self):
        assert tuple(sorted(self.checkpoints)) == self.checkpoints
        # need at least 1 cp on the upper row and 1 on the lower row
        assert len(self.checkpoints) > 1

    @classmethod
    def from_kml_folder(cls, folder: kml.Folder):
        name = str(folder.name)
        # name should be 'Etappe_4_fietsen' format
        assert name.count("_") == 2
        # etappe_4_hardlopen should result in idx 4
        idx = int(name.split("_")[-2])
        # and kind 'hardlopen'
        kind = name.split("_")[-1]
        cps = []
        for i, placemark in enumerate(folder.features()):
            cp_data = {
                e.name.lower(): e.value for e in placemark.extended_data.elements
            }
            point = placemark.geometry
            cps.append(
                CheckPoint(
                    idx=i + 1,
                    score=int(float(cp_data["score"])),
                    hint=str(cp_data["hint"]),
                    hidden=bool(int(cp_data["hidden"])),
                    coordinate=WKT_converter.to_amersfoort(point.x, point.y),
                )
            )
        return cls(idx=idx, kind=kind, checkpoints=tuple(cps))

    def __lt__(self, other) -> bool:
        return bool(self.idx < other)

    def __gt__(self, other) -> bool:
        return bool(self.idx > other)

    def __len__(self) -> int:
        return len(self.checkpoints)

    def __iter__(self) -> Iterator[CheckPoint]:
        return iter(self.checkpoints)

    def __getitem__(self, key) -> CheckPoint:
        return self.checkpoints[key]

    def __contains__(self, __x: object) -> bool:
        return bool(__x in self.checkpoints)


def strip_hidden_cp_from_kml(path: str | Path) -> None:
    path = Path(path).resolve()
    assert path.suffix == ".kml"
    with open(path, "rb") as old_folder:
        kml_str = old_folder.read()
    old_kml = kml.KML()
    old_kml.from_string(kml_str)
    old_doc = list(old_kml.features())[0]
    old_folders: Generator[kml.Folder] = old_doc.features()
    new_kml = kml.KML()
    new_doc = kml.Document(old_doc.ns, name=old_doc.name)
    new_kml.append(new_doc)
    for old_folder in old_folders:
        assert isinstance(old_folder, kml.Folder)
        new_folder = kml.Folder(
            ns=old_folder.ns,
            id=old_folder.id,
            name=old_folder.name,
            description=old_folder.description,
            styles=old_folder.styles(),
            styleUrl=old_folder.styleUrl,
        )
        # this is an actual etappe folder
        if str(old_folder.name).count("_") == 2:
            for placemark in old_folder.features():
                cp_data = {
                    e.name.lower(): e.value for e in placemark.extended_data.elements
                }
                if not bool(int(cp_data["hidden"])):
                    new_folder.append(placemark)
        else:
            for feature in old_folder.features():
                new_folder.append(feature)
        new_doc.append(new_folder)
    fn = path.parent / Path(path.stem + "hidden_removed").with_suffix(".kml")
    with open(fn, "w") as f:
        f.write(new_kml.to_string())
    print(f"Wrote new kml with hidden cps removed to {fn}")
