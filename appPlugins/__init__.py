import logging

try:
    from appPlugins.ToolImage import ToolImage
except ImportError as err:
    logging.getLogger('base').warning("ToolImage plugin could not be loaded: %s", err)
    # ToolImage will be unavailable but app continues

from appPlugins.ToolCalculators.Calculator import ToolCalculator

from appPlugins.ToolDblSided import DblSidedTool
from appPlugins.ToolExtract import ToolExtract
from appPlugins.ToolAlignObjects.Align import AlignObjects

from appPlugins.ToolFilm import Film

from appPlugins.ToolDistance import Distance
from appPlugins.ToolObjectDistance import ObjectDistance

from appPlugins.ToolMove import ToolMove

from appPlugins.ToolCutOut import CutOut
from appPlugins.ToolNCC.Ncc import ToolNcc
from appPlugins.ToolPaint.Paint import ToolPaint
from appPlugins.ToolPhotoCarve.PhotoCarve import ToolPhotoCarve
from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
from appPlugins.ToolIsolation import ToolIsolation
from appPlugins.ToolFollow import ToolFollow
from appPlugins.ToolDrilling import ToolDrilling
from appPlugins.ToolMilling import ToolMilling
from appPlugins.ToolLevelling import ToolLevelling
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender

from appPlugins.ToolOptimal import ToolOptimal

from appPlugins.ToolPanelize import Panelize
from appPlugins.ToolPcbWizard import PcbWizard
from appPlugins.ToolPdf.PdfImport import ToolPDF
from appPlugins.ToolReport import ObjectReport

from appPlugins.ToolQRCode import QRCode
from appPlugins.ToolRulesCheck import RulesCheck

from appPlugins.ToolCopperThieving import ToolCopperThieving
from appPlugins.ToolFiducials import ToolFiducials

from appPlugins.ToolShell import FCShell
from appPlugins.ToolSolderPaste import SolderPaste
from appPlugins.ToolSub import ToolSub
from appPlugins.ToolCompare import ToolCompare

from appPlugins.ToolTransform import ToolTransform
from appPlugins.ToolPunchGerber import ToolPunchGerber

from appPlugins.ToolInvertGerber import ToolInvertGerber
from appPlugins.ToolMarkers import ToolMarkers
from appPlugins.ToolEtchCompensation import ToolEtchCompensation
